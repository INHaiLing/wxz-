"""Real SQLite -> isolated PostgreSQL 16 rehearsal; never migrate a live DB."""

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import uuid

from cryptography.fernet import Fernet
import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
TARGET_PATTERN = re.compile(r"transfer_check_[a-f0-9]{32}\Z")


class VerificationFailure(RuntimeError):
    """Only controlled diagnostic text may be displayed by the rehearsal."""

FIXTURE = r'''
import base64, os
from datetime import date
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from accounts.services import issue_student_session
from accounts.wechat import WeChatLogin
from content.models import LearningConfiguration, Question
from content.services import publish_questions, make_publication_token
from entitlements.services import grant_entitlement, revoke_entitlement
from learning.services import update_preferences, update_question_state
from practice.services import create_round, save_resume
settings.STUDENT_SESSION_ENCRYPTION_KEYS = (os.environ["TRANSFER_FIXTURE_KEY"],)
actor = get_user_model().objects.create_user(username="transfer-test-operator", password=None, is_staff=True)
actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="content", codename="publish_question"))
actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="entitlements", codename="revoke_entitlementsource"))
actor.groups.add(Group.objects.get(name="业务查看"))
LearningConfiguration.objects.create(page_size=3, daily_target=29, exam_date=date(2026, 12, 1))
question = Question.objects.order_by("pk").first()
query = Question.objects.filter(pk=question.pk)
publish_questions(query, actor, confirmation_token=make_publication_token(query, actor, "publish"))
question.refresh_from_db()
question.answers = [answer + "修订" for answer in question.answers]
question.save()
publish_questions(query, actor, confirmation_token=make_publication_token(query, actor, "publish"))
question.refresh_from_db()
question.answers = [answer + "未发布草稿" for answer in question.answers]
question.save()
token, session = issue_student_session(WeChatLogin("wx-transfer-fixture", "transfer-fixture-student", base64.b64encode(b"fixture-session-key").decode()))
student = session.user
revoked = grant_entitlement(student, "activation", "transfer-revoked-source")
revoke_entitlement(revoked.pk, actor, "隔离迁移验证：撤销历史")
grant_entitlement(student, "payment", "transfer-active-source")
update_preferences(student, {"mode": "reciting", "dailyTarget": 31, "baseVersion": 0}, "transfer-preference-key")
update_question_state(student, question.pk, {"favorite": True, "mastered": True, "baseVersion": 0}, "transfer-question-key")
context = {"source": question.source, "categoryId": question.category_id, "articleId": question.article_id, "type": None, "mode": "writing"}
round_ = create_round(student, context, "transfer-round-key")
save_resume(student, {**context, "questionId": question.pk, "roundId": round_["id"], "groupIndex": 1, "baseVersion": 0}, "transfer-resume-key")
'''

VERIFY = r'''
import base64, os
from django.conf import settings
from django.db import connection, IntegrityError, transaction
from django.utils import timezone
from accounts.crypto import decrypt_session_key
from accounts.models import StudentSession
from content.models import LearningConfiguration, Question
from entitlements.models import EntitlementSource
settings.STUDENT_SESSION_ENCRYPTION_KEYS = (os.environ["TRANSFER_FIXTURE_KEY"],)
connection.check_constraints()
assert Question.objects.count() == 16
question = Question.objects.get(is_published=True)
assert question.revisions.count() == 2 and question.published_revision.version == 2
assert question.published_revision.question_id == question.pk
assert question.answers != question.published_revision.answers
assert EntitlementSource.objects.filter(status="active", expires_at__isnull=True).count() == 1
assert EntitlementSource.objects.filter(status="revoked", revoked_at__isnull=False, expires_at__isnull=True).count() == 1
session = StudentSession.objects.get()
assert decrypt_session_key(session.encrypted_session_key) == base64.b64encode(b"fixture-session-key").decode()
assert session.identity.user_id == session.user_id
for operation in (
    lambda: LearningConfiguration._base_manager.filter(pk=1).update(page_size=101),
    lambda: EntitlementSource._base_manager.filter(status="active").update(expires_at=timezone.now()),
):
    try:
        with transaction.atomic():
            operation()
    except IntegrityError:
        pass
    else:
        raise AssertionError("Business database constraint not enforced")
'''


def run(*arguments, env, stage):
    try:
        subprocess.run([sys.executable, "manage.py", *arguments], cwd=ROOT, env=env,
                       check=True, capture_output=True, timeout=90)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Private command output may contain serialized data. Do not echo it.
        raise VerificationFailure(f"迁移演练阶段失败：{stage}；未输出私有数据或连接配置。") from None


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump(path, env):
    # Exclusive private file before Django opens --output; never a tracked path.
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    run("dumpdata", "--natural-foreign", "--exclude", "contenttypes", "--exclude", "auth.permission",
        "--output", str(path), env=env, stage="受控业务导出")
    os.chmod(path, 0o600)


def payload(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    # Natural permissions can have different numeric PK/order across engines.
    for record in data:
        for field in ("permissions", "user_permissions", "groups"):
            if field in record["fields"]:
                record["fields"][field].sort(key=lambda value: json.dumps(value, sort_keys=True))
    return sorted(data, key=lambda record: (record["model"], str(record.get("pk"))))


def sqlite_integrity(path):
    db = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)
    try:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",) or db.execute("PRAGMA foreign_key_check").fetchall():
            raise VerificationFailure("SQLite 演练源／快照完整性异常。")
    finally:
        db.close()


def assert_owned_target(target, created_targets, configured_name):
    if target not in created_targets or not TARGET_PATTERN.fullmatch(target) or target in {
        configured_name, "test_" + configured_name, "postgres", "template0", "template1",
    }:
        raise VerificationFailure("拒绝清理未经脚本创建的目标数据库。")


def main():
    names = ("TEST_DB_NAME", "TEST_DB_USER", "TEST_DB_PASSWORD", "TEST_DB_HOST")
    if any(not os.environ.get(name) for name in names):
        raise VerificationFailure("迁移演练需要显式独立 TEST_DB_NAME/USER/PASSWORD/HOST。")
    configured_name = os.environ["TEST_DB_NAME"]
    target = "transfer_check_" + uuid.uuid4().hex
    created_targets = set()
    parameters = {"dbname": configured_name, "user": os.environ["TEST_DB_USER"],
                  "password": os.environ["TEST_DB_PASSWORD"], "host": os.environ["TEST_DB_HOST"],
                  "port": os.environ.get("TEST_DB_PORT", "5432"), "connect_timeout": 5,
                  "options": "-c lock_timeout=5000 -c statement_timeout=30000"}
    env = dict(os.environ, PYTHONUTF8="1", DJANGO_SETTINGS_MODULE="config.test_settings",
               TEST_DB_ENGINE="sqlite", TRANSFER_FIXTURE_KEY=Fernet.generate_key().decode())
    with TemporaryDirectory(prefix="chinese-study-transfer-") as directory:
        folder = Path(directory).resolve()
        if os.name != "nt":
            os.chmod(folder, 0o700)
        source, snapshot = folder / "source.sqlite3", folder / "snapshot.sqlite3"
        original, snap_dump, transferred = folder / "source.json", folder / "snapshot.json", folder / "target.json"
        env["TEST_SQLITE_PATH"] = str(source)
        for command in (("migrate", "--noinput"), ("seed_demo",), ("setup_roles",), ("setup_business_roles",), ("seed_product",)):
            run(*command, env=env, stage=command[0])
        run("shell", "--no-imports", "--command", FIXTURE, env=env, stage="生成隔离测试业务数据")
        run("shell", "--no-imports", "--command", VERIFY, env=env, stage="源库业务约束")
        run("backup_database", "--output", str(snapshot), env=env, stage="SQLite 一致快照")
        source_hash, snapshot_hash = file_hash(source), file_hash(snapshot)
        sqlite_integrity(source)
        sqlite_integrity(snapshot)
        dump(original, env)
        snapshot_env = dict(env, TEST_SQLITE_PATH=str(snapshot))
        dump(snap_dump, snapshot_env)
        expected = payload(original)
        if payload(snap_dump) != expected:
            raise VerificationFailure("SQLite 快照业务载荷不一致。")
        with psycopg.connect(autocommit=True, **parameters) as control:
            version = control.execute("SHOW server_version_num").fetchone()[0]
            if not 160000 <= int(version) < 170000:
                raise VerificationFailure("本演练需要真实 PostgreSQL 16。")
            control.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
            created_targets.add(target)
            try:
                pg_env = dict(env, TEST_DB_ENGINE="postgresql", TEST_DB_NAME=target)
                run("migrate", "--noinput", env=pg_env, stage="空 PostgreSQL 迁移")
                run("loaddata", str(original), env=pg_env, stage="实际 SQLite→PostgreSQL 数据导入")
                run("shell", "--no-imports", "--command", VERIFY, env=pg_env, stage="目标业务引用、密文与约束")
                dump(transferred, pg_env)
                if payload(transferred) != expected:
                    raise VerificationFailure("PostgreSQL 业务载荷与 SQLite 源不一致。")
                if file_hash(source) != source_hash or file_hash(snapshot) != snapshot_hash:
                    raise VerificationFailure("演练改变了 SQLite 源或快照。")
                sqlite_integrity(source)
                sqlite_integrity(snapshot)
            finally:
                assert_owned_target(target, created_targets, configured_name)
                control.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(target)))
                created_targets.remove(target)
    print("PASS: actual SQLite→PostgreSQL 16 transfer; complete payload, stable IDs, revisions, roles, encrypted sessions and active/revoked sources; originals unchanged; owned target cleaned.")


if __name__ == "__main__":
    try:
        main()
    except VerificationFailure as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Includes driver errors which could otherwise echo a host or credential.
        print("FAIL: isolated database transfer verification; inspect configuration in a private environment.", file=sys.stderr)
        sys.exit(1)
