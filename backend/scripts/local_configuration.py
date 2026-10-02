"""Private local configuration primitives; no HTTP calls or business writes."""

from contextlib import contextmanager
import base64
import getpass
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import tempfile
import warnings

from cryptography.fernet import Fernet
from dotenv.parser import parse_stream


SECRET_FIELDS = (
    "WECHAT_APP_SECRET", "VIRTUAL_PAYMENT_APP_KEY",
    "VIRTUAL_PAYMENT_CALLBACK_TOKEN", "VIRTUAL_PAYMENT_CALLBACK_AES_KEY",
)
OPTIONAL_SECRET_FIELDS = ("VIRTUAL_PAYMENT_SANDBOX_APP_KEY",)
SALES_FIELDS = ("VIRTUAL_PAYMENT_ENABLED", "VIRTUAL_PAYMENT_ANDROID_ENABLED", "VIRTUAL_PAYMENT_IOS_ENABLED")
CONFIRMATIONS = ("filing", "virtualPayment", "appleIap", "developerMember")
APP_PATTERN = re.compile(r"wx[0-9a-f]{16}\Z")
OFFER_PATTERN = re.compile(r"[0-9]{1,20}\Z")


class LocalConfigurationError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def fail(code, message):
    raise LocalConfigurationError(code, message)


def read_private(path):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        fail("CONFIG_SYMLINK", "拒绝符号链接配置文件或目录。")
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 65536:
        fail("CONFIG_FILE_INVALID", "配置文件类型或大小无效。")
    try:
        return path.read_bytes()
    except OSError:
        fail("CONFIG_READ_FAILED", "无法读取私有配置。")


def file_stamp(path):
    path = Path(path)
    if not path.exists():
        return None
    stat = path.lstat()
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size


def snapshot_file(path):
    before = file_stamp(path)
    raw = read_private(path)
    after = file_stamp(path)
    if before != after:
        fail("CONFIG_CHANGED", "配置读取时被其他操作修改，请重新核对。")
    return raw, after


def parse_env(raw):
    try:
        text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
        bindings = list(parse_stream(io.StringIO(text)))
    except (UnicodeError, ValueError):
        fail("ENV_INVALID", "私有配置编码或语法无效。")
    values = {}
    for binding in bindings:
        if binding.error:
            fail("ENV_INVALID", "私有配置语法无效。")
        if binding.key is not None:
            if binding.key in values:
                fail("ENV_DUPLICATE", "私有配置存在重复字段，请先人工核对。")
            values[binding.key] = binding.value or ""
    return text, bindings, values


def secret_valid(name, value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    if name == "VIRTUAL_PAYMENT_CALLBACK_AES_KEY":
        try:
            return len(value) == 43 and len(base64.b64decode(value + "=", validate=True)) == 32
        except ValueError:
            return False
    return True


def key_ring_valid(value):
    keys = [key.strip() for key in value.split(",") if key.strip()]
    if not 1 <= len(keys) <= 10:
        return False
    try:
        for key in keys:
            cipher = Fernet(key)
            if cipher.decrypt(cipher.encrypt(b"local-readiness")) != b"local-readiness":
                return False
    except (ValueError, TypeError):
        return False
    return True


def hidden_secret(name):
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        fail("SECRET_TTY_REQUIRED", "秘密录入必须使用本机交互终端，禁止明文降级。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass(f"{name}（隐藏输入，空值跳过）：")
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        fail("SECRET_INPUT_ABORTED", "秘密录入已取消，配置未提交。")


def load_profile(raw):
    if raw is None:
        return None
    def distinct_pairs(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError()
            result[name] = value
        return result
    try:
        profile = json.loads(raw, object_pairs_hook=distinct_pairs)
        if set(profile) != {"schemaVersion", "appId", "offerId", "platformConfirmed"} or type(profile["schemaVersion"]) is not int or profile["schemaVersion"] != 1:
            raise ValueError()
        confirmed = profile["platformConfirmed"]
        if set(confirmed) != set(CONFIRMATIONS) or any(type(confirmed[key]) is not bool for key in CONFIRMATIONS):
            raise ValueError()
        if not APP_PATTERN.fullmatch(profile["appId"]) or not OFFER_PATTERN.fullmatch(profile["offerId"]):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        fail("PROFILE_INVALID", "本机账号档案结构无效；不能包含秘密或额外字段。")
    return profile


def quote_env(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def render_env(bindings, changes):
    remaining = dict(changes)
    parts = []
    for binding in bindings:
        if binding.key in remaining:
            parts.append(f"{binding.key}={quote_env(remaining.pop(binding.key))}\n")
        else:
            parts.append(binding.original.string)
    text = "".join(parts)
    if text and not text.endswith("\n"):
        text += "\n"
    text += "".join(f"{name}={quote_env(value)}\n" for name, value in remaining.items())
    return text.encode("utf-8")


def probe_database(directory, values, *, details=False):
    """Open only an existing SQLite file or explicit read-only PostgreSQL session."""
    engine = values.get("DB_ENGINE", "sqlite")
    result = {"engine": engine, "tables": set(), "encrypted": False, "appIds": set(), "activeOrders": False}
    if engine == "sqlite":
        path = Path(values.get("SQLITE_PATH", "db.sqlite3"))
        if not path.is_absolute():
            path = Path(directory) / path
        if not path.exists():
            result["exists"] = False
            return result
        if path.is_symlink() or not path.is_file():
            fail("DATABASE_UNAVAILABLE", "无法安全检查本机数据库。")
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    elif engine == "postgresql":
        import psycopg
        if not all(values.get(name) for name in ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST")):
            fail("DATABASE_UNAVAILABLE", "数据库连接配置不完整。")
        connection = psycopg.connect(dbname=values["DB_NAME"], user=values["DB_USER"], password=values["DB_PASSWORD"],
                                    host=values["DB_HOST"], port=values.get("DB_PORT", "5432"), connect_timeout=2,
                                    options="-c default_transaction_read_only=on -c statement_timeout=2000")
    else:
        fail("DATABASE_ENGINE_INVALID", "只支持 SQLite 或 PostgreSQL 本机数据库。")
    try:
        cursor = connection.cursor()
        query = lambda statement: cursor.execute(statement).fetchall()
        result["exists"] = True
        result["tables"] = {row[0] for row in query(
            "SELECT name FROM sqlite_master WHERE type='table'" if engine == "sqlite"
            else "SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")}
        for table, column in (("accounts_wechatidentity", "encrypted_session_key"),
                              ("accounts_studentsession", "encrypted_session_key"),
                              ("payments_accesstokencache", "ciphertext")):
            if table in result["tables"] and query(f'SELECT 1 FROM "{table}" WHERE "{column}" IS NOT NULL AND "{column}" <> \'\' LIMIT 1'):
                result["encrypted"] = True
        for table in ("accounts_wechatidentity", "payments_order", "payments_accesstokencache"):
            if table in result["tables"]:
                result["appIds"].update(row[0] for row in query(f'SELECT DISTINCT app_id FROM "{table}"') if row[0])
        if "payments_order" in result["tables"]:
            result["activeOrders"] = bool(query("SELECT 1 FROM payments_order WHERE status IN ('preparing','paid','review') LIMIT 1"))
        if details:
            result["applied"] = set(query("SELECT app, name FROM django_migrations")) if "django_migrations" in result["tables"] else set()
            result["admin"] = bool(query("SELECT 1 FROM accounts_user WHERE is_superuser AND is_active LIMIT 1")) if "accounts_user" in result["tables"] else False
            result["roles"] = {}
            required = {"auth_group", "auth_group_permissions", "auth_permission", "django_content_type"}
            if required <= result["tables"]:
                for name, app, model, code in query("SELECT g.name,c.app_label,c.model,p.codename FROM auth_group g LEFT JOIN auth_group_permissions gp ON gp.group_id=g.id LEFT JOIN auth_permission p ON p.id=gp.permission_id LEFT JOIN django_content_type c ON c.id=p.content_type_id"):
                    result["roles"].setdefault(name, set())
                    if code:
                        result["roles"][name].add((app, model, code))
            result["product"] = None
            if "entitlements_product" in result["tables"]:
                rows = query("SELECT is_active,price_fen,platform_product_id,platform_sync_state,platform_synced_price_fen FROM entitlements_product WHERE id='all-chinese'")
                if rows:
                    result["product"] = dict(zip(("active", "price", "mapping", "sync", "syncedPrice"), rows[0]))
        return result
    finally:
        connection.close()


def safe_probe(directory, values, *, details=False):
    try:
        return probe_database(directory, values, details=details)
    except LocalConfigurationError:
        raise
    except Exception:
        fail("DATABASE_UNAVAILABLE", "数据库不可达或结构暂不可检查；未输出连接详情。")


def reject_process_ambiguity(values):
    defaults = {"DB_ENGINE": "sqlite", "SQLITE_PATH": "db.sqlite3", "DB_NAME": "chinese_study",
                "DB_USER": "chinese_study", "DB_PASSWORD": "", "DB_HOST": "127.0.0.1", "DB_PORT": "5432",
                "WECHAT_APP_ID": "", "VIRTUAL_PAYMENT_OFFER_ID": "", "STUDENT_SESSION_ENCRYPTION_KEYS": ""}
    defaults.update(dict.fromkeys(SECRET_FIELDS + OPTIONAL_SECRET_FIELDS, ""))
    for name, default in defaults.items():
        if name in os.environ and os.environ[name] != values.get(name, default):
            fail("PROCESS_ENV_AMBIGUOUS", "进程环境与待写文件的账号、数据库或密钥配置存在歧义，请先人工核对。")


def private_temp(path, data):
    path = Path(path)
    folder = path.parent if path.parent.name == ".local" else path.parent / ".local"
    if folder.is_symlink() or folder.parent.is_symlink():
        fail("CONFIG_SYMLINK", "拒绝符号链接秘密暂存目录。")
    folder.mkdir(mode=0o700, exist_ok=True)
    if not folder.is_dir():
        fail("CONFIG_FILE_INVALID", "秘密暂存目录不可用。")
    if os.name != "nt":
        os.chmod(folder, 0o700)
    # Interrupted writes stay under the repository's ignored private directory.
    descriptor, name = tempfile.mkstemp(prefix=".config-", dir=folder)
    try:
        os.chmod(name, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1  # fdopen now owns the descriptor, including on failure.
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return Path(name)
    except BaseException:
        os.close(descriptor) if descriptor >= 0 else None
        Path(name).unlink(missing_ok=True)
        raise


def commit_files(env_path, old_env, new_env, profile_path, old_profile, new_profile, stamps):
    temporary = []
    profile_replaced = False
    own_profile_stamp = None
    try:
        for path, data in ((env_path, new_env), (profile_path, new_profile)):
            temporary.append(private_temp(path, data))
        if read_private(env_path) != old_env or read_private(profile_path) != old_profile or (file_stamp(env_path), file_stamp(profile_path)) != stamps:
            fail("CONFIG_CHANGED", "配置已被其他操作修改，请重新核对后执行。")
        os.replace(temporary[1], profile_path)
        profile_replaced = True
        own_profile_stamp = file_stamp(profile_path)
        if read_private(env_path) != old_env or file_stamp(env_path) != stamps[0]:
            fail("CONFIG_CHANGED", "配置在提交期间被其他操作修改；保留人工修改并回滚本次档案。")
        os.replace(temporary[0], env_path)
    except BaseException:
        if profile_replaced:
            if read_private(profile_path) != new_profile or file_stamp(profile_path) != own_profile_stamp:
                fail("CONFIG_ROLLBACK_CHANGED", "回滚时账号档案已被人工修改，已保留人工版本；请核对档案与原配置。")
            if old_profile is None:
                profile_path.unlink(missing_ok=True)
            else:
                recovery = private_temp(profile_path, old_profile)
                try:
                    os.replace(recovery, profile_path)
                finally:
                    recovery.unlink(missing_ok=True)
        raise
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)


@contextmanager
def configuration_lock(directory):
    folder = Path(directory) / ".local"
    if folder.is_symlink():
        fail("CONFIG_SYMLINK", "拒绝符号链接配置目录。")
    folder.mkdir(mode=0o700, exist_ok=True)
    lock = folder / "configure.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        fail("CONFIG_BUSY", "另一次配置正在执行；未改动私有配置。")
    try:
        os.close(descriptor)
        yield folder
    finally:
        lock.unlink(missing_ok=True)


def configure(directory, app_id, offer_id, confirmations=None, *, fill_missing=False, update_fields=(), prompt=hidden_secret):
    directory = Path(directory)
    if not isinstance(app_id, str) or not APP_PATTERN.fullmatch(app_id) or not isinstance(offer_id, str) or not OFFER_PATTERN.fullmatch(offer_id):
        fail("ACCOUNT_IDENTIFIERS_INVALID", "AppID 或 OfferID 格式无效。")
    if any(name not in SECRET_FIELDS + OPTIONAL_SECRET_FIELDS for name in update_fields):
        fail("SECRET_FIELD_INVALID", "只能主动更新指定微信或支付秘密字段。")
    with configuration_lock(directory) as folder:
        env_path, profile_path = directory / ".env", folder / "wechat-account.json"
        old_env, env_stamp = snapshot_file(env_path)
        old_profile, profile_stamp = snapshot_file(profile_path)
        profile = load_profile(old_profile)
        source = old_env if old_env is not None else read_private(directory / ".env.example")
        if source is None:
            fail("ENV_TEMPLATE_MISSING", "缺少本机配置模板。")
        _, bindings, values = parse_env(source)
        reject_process_ambiguity(values)
        if values.get("WECHAT_APP_ID") not in (None, "", app_id) or profile and (profile["appId"] != app_id or profile["offerId"] != offer_id):
            fail("ACCOUNT_SWITCH_BLOCKED", "已有账号配置不同，禁止自动切换应用或 OfferID。")
        if values.get("VIRTUAL_PAYMENT_OFFER_ID") not in (None, "", offer_id):
            fail("ACCOUNT_SWITCH_BLOCKED", "已有 OfferID 不同，禁止自动切换。")
        for name in SALES_FIELDS:
            if values.get(name, "false").lower() not in {"0", "false", ""}:
                fail("SALES_ALREADY_ENABLED", "已有销售开关启用或无效，禁止隐式改动；请先人工核对。")
            if os.environ.get(name, "false").lower() not in {"0", "false", ""}:
                fail("SALES_ALREADY_ENABLED", "进程环境存在已启用销售配置；请先人工核对。")
        if os.environ.get("WECHAT_APP_ID", "") not in ("", app_id):
            fail("ACCOUNT_SWITCH_BLOCKED", "进程环境关联其他应用，禁止自动切换。")
        data = safe_probe(directory, values)
        if data["appIds"] - {app_id}:
            fail("ACCOUNT_DATA_MISMATCH", "已有业务数据关联其他应用，不能继承或自动切换。")
        changes = {"WECHAT_APP_ID": app_id, "VIRTUAL_PAYMENT_OFFER_ID": offer_id}
        changes.update({name: "false" for name in SALES_FIELDS})
        if not values.get("DJANGO_SECRET_KEY") or old_env is None and values.get("DJANGO_SECRET_KEY", "").startswith("replace-"):
            changes["DJANGO_SECRET_KEY"] = secrets.token_urlsafe(64)
        if not values.get("STUDENT_SESSION_ENCRYPTION_KEYS"):
            if data["encrypted"]:
                fail("ENCRYPTION_KEY_RECOVERY_REQUIRED", "存在旧加密数据，请恢复原会话密钥；禁止自动换钥。")
            changes["STUDENT_SESSION_ENCRYPTION_KEYS"] = Fernet.generate_key().decode("ascii")
        elif not key_ring_valid(values["STUDENT_SESSION_ENCRYPTION_KEYS"]):
            fail("ENCRYPTION_KEY_INVALID", "已有会话密钥格式无效；请恢复原密钥，不自动替换。")
        for name in SECRET_FIELDS + OPTIONAL_SECRET_FIELDS:
            if name not in update_fields and values.get(name) and not secret_valid(name, values[name]):
                fail("SECRET_FORMAT_INVALID", "已有秘密结构无效；请人工恢复，不自动覆盖。")
            if name in update_fields or fill_missing and name in SECRET_FIELDS and not values.get(name):
                secret = prompt(name)
                if secret:
                    if not secret_valid(name, secret):
                        fail("SECRET_FORMAT_INVALID", "秘密结构无效；整次配置未提交。")
                    changes[name] = secret
            if name not in values and name not in changes:
                changes[name] = ""
        if "VIRTUAL_PAYMENT_ENV" not in values:
            changes["VIRTUAL_PAYMENT_ENV"] = "0"
        elif values["VIRTUAL_PAYMENT_ENV"] not in ("0", "1"):
            fail("PAYMENT_ENV_INVALID", "支付环境配置无效。")
        confirmed = dict(profile["platformConfirmed"] if profile else dict.fromkeys(CONFIRMATIONS, False))
        for name, flag in (confirmations or {}).items():
            if name not in CONFIRMATIONS or type(flag) is not bool:
                fail("PROFILE_INVALID", "平台确认字段无效。")
            if flag:
                confirmed[name] = True
        new_profile = {"schemaVersion": 1, "appId": app_id, "offerId": offer_id, "platformConfirmed": confirmed}
        new_env = render_env(bindings, changes)
        _, _, final = parse_env(new_env)
        current_data = safe_probe(directory, values)
        if current_data["appIds"] - {app_id}:
            fail("ACCOUNT_DATA_MISMATCH", "已有业务数据关联其他应用，不能继承或自动切换。")
        if "STUDENT_SESSION_ENCRYPTION_KEYS" in changes and current_data["encrypted"]:
            fail("ENCRYPTION_KEY_RECOVERY_REQUIRED", "存在旧加密数据，请恢复原会话密钥；禁止自动换钥。")
        if current_data["activeOrders"] and any(name in changes and changes[name] != values.get(name, "") for name in SECRET_FIELDS + OPTIONAL_SECRET_FIELDS):
            fail("ACTIVE_TRANSACTION_BLOCKED", "存在活动交易，不能通过本机准备工具更新支付秘密。")
        commit_files(env_path, old_env, new_env, profile_path, old_profile,
                     (json.dumps(new_profile, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), (env_stamp, profile_stamp))
        return [name for name in SECRET_FIELDS if not final.get(name)]
