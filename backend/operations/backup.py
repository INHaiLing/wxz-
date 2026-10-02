"""Offline restore into new targets only; never replace the configured live DB."""
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import closing

from django.core.management.base import CommandError
from django.db import connections


def _run(args, config):
    executable = shutil.which(args[0])
    if not executable:
        raise CommandError(f"缺少 {args[0]}，请安装 PostgreSQL 16 客户端工具。")
    env = dict(os.environ)
    env["PGPASSWORD"] = str(config.get("PASSWORD", ""))
    env["PGCONNECT_TIMEOUT"] = "10"
    try:
        return subprocess.run([executable, *args[1:]], env=env, capture_output=True,
                              check=True, timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        # Tool output could contain credentials or PII: leave investigation local.
        raise CommandError("数据库工具执行失败或超时；未输出连接配置，请在受控环境检查工具版本与权限。") from error


def _pg_args(config, database):
    # Passing connection fields individually avoids URI interpolation.
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", str(database)):
        raise CommandError("数据库名须为字母开头的 1～63 位字母数字下划线。")
    return ["--host", str(config.get("HOST") or "127.0.0.1"),
            "--port", str(config.get("PORT") or "5432"),
            "--username", str(config.get("USER") or ""), "--dbname", str(database)]


def _sqlite_check(path):
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise CommandError("SQLite 完整性检查失败。")
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='django_migrations'").fetchone():
                raise CommandError("备份不包含 Django 迁移表。")
    except sqlite3.DatabaseError as error:
        raise CommandError("不是可恢复的 SQLite 备份。") from error


def backup_database(output, *, alias="default"):
    config = connections[alias].settings_dict
    output = Path(output).expanduser().absolute()
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive target allocation prevents overwriting a previous backup.
    try:
        handle = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(handle)
    except FileExistsError as error:
        raise CommandError("目标文件已存在，拒绝覆盖。") from error
    fd, temp_name = tempfile.mkstemp(prefix=".backup-", dir=output.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        if config["ENGINE"] == "django.db.backends.sqlite3":
            source = Path(config["NAME"]).absolute()
            if output.resolve() == source.resolve():
                raise CommandError("备份不能写入当前数据库。")
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as source_db:
                with closing(sqlite3.connect(temp)) as target_db:
                    source_db.backup(target_db)
            _sqlite_check(temp)
        elif config["ENGINE"] == "django.db.backends.postgresql":
            _run(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(temp),
                  *_pg_args(config, config["NAME"])], config)
        else:
            raise CommandError("只支持 SQLite 与 PostgreSQL。")
        os.chmod(temp, 0o600)
        os.replace(temp, output)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        temp.unlink(missing_ok=True)
    return output


def restore_database(source, target, *, alias="default"):
    config = connections[alias].settings_dict
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise CommandError("备份文件不存在。")
    if config["ENGINE"] == "django.db.backends.sqlite3":
        target = Path(target).expanduser().absolute()
        if target.resolve() == Path(config["NAME"]).resolve() or target.resolve() == source:
            raise CommandError("恢复必须指定独立目标，不能覆盖当前库或备份。")
        _sqlite_check(source)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            handle = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            raise CommandError("恢复目标已存在，拒绝覆盖。") from error
        try:
            with os.fdopen(handle, "wb") as destination, source.open("rb") as backup:
                shutil.copyfileobj(backup, destination)
            _sqlite_check(target)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target
    if config["ENGINE"] != "django.db.backends.postgresql":
        raise CommandError("只支持 SQLite 与 PostgreSQL。")
    if str(target) == str(config["NAME"]):
        raise CommandError("恢复目标不能是当前数据库。")
    _pg_args(config, target)
    import psycopg
    # Refuse nonempty databases; no DROP/CLEAN/CREATE command is issued here.
    try:
        with psycopg.connect(dbname=target, user=config.get("USER"), password=config.get("PASSWORD"),
                              host=config.get("HOST") or "127.0.0.1", port=config.get("PORT") or 5432,
                              connect_timeout=10) as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%' AND c.relkind IN ('r','p','v','m','S','f') LIMIT 1")
                if cursor.fetchone():
                    raise CommandError("恢复目标不是空数据库，拒绝覆盖。")
    except psycopg.Error as error:
        raise CommandError("无法检查独立目标库，请预先创建空库并核对权限。") from error
    _run(["pg_restore", "--single-transaction", "--exit-on-error", "--no-owner", "--no-acl",
          *_pg_args(config, target), str(source)], config)
    return target
