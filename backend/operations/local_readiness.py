"""Offline fixed-code report. Never disclose configuration or business values."""
from pathlib import Path

from django.conf import settings
from django.db.migrations.loader import MigrationLoader

from scripts.local_configuration import (APP_PATTERN, OFFER_PATTERN, SALES_FIELDS, SECRET_FIELDS,
    LocalConfigurationError, key_ring_valid, load_profile, parse_env, read_private, safe_probe, secret_valid)
from .management.commands.setup_business_roles import ROLES


EXTERNAL_CHECKS = (
    ("EXTERNAL_WECHAT_LOGIN", "真实微信 code 登录尚需开发者工具或甲方前端验收。"),
    ("EXTERNAL_PRODUCT_CONFIRMATION", "商品映射及价格须与微信平台逐项核对；离线配置不代替平台验证。"),
    ("EXTERNAL_HTTPS_CALLBACK", "公网 HTTPS 回调与合法请求域名需真实环境验收，本轮仅本机。"),
    ("EXTERNAL_ANDROID_PAYMENT", "Android 真机支付、取消及退款流程需真实渠道验收。"),
    ("EXTERNAL_IOS_PAYMENT", "Apple 已开通的人工确认不代替 iOS 真机支付与退款验收。"),
    ("EXTERNAL_CATEGORY", "实际经营类目仍需在微信平台核对。"),
    ("EXTERNAL_POSTGRESQL_CONCURRENCY", "本机配置检查不执行交易并发验收；PostgreSQL 专项证据单独记录。"),
)

CONTENT_VIEW = {"view_category", "view_article", "view_question", "view_questionrevision"}
CONTENT_ROLES = {
    name: {("content", code.split("_", 1)[1], code) for code in codes}
    for name, codes in {
        "题库查看": CONTENT_VIEW,
        "题库编辑": CONTENT_VIEW | {"add_category", "change_category", "add_article", "change_article",
                                     "add_question", "change_question", "import_question"},
        "题库发布": CONTENT_VIEW | {"publish_question"},
    }.items()
}


def collect_readiness(directory=None):
    directory = Path(directory or settings.BASE_DIR)
    checks = []

    def check(code, status, message):
        checks.append({"code": code, "status": status, "message": message})

    file_values = {}
    profile = None
    try:
        raw = read_private(directory / ".env")
        if raw is None:
            check("CONFIG_FILE", "missing", "未创建本机私有配置，请先初始化。")
        else:
            _, _, file_values = parse_env(raw)
            check("CONFIG_FILE", "ready", "私有配置语法清晰，无重复字段。")
        profile = load_profile(read_private(directory / ".local" / "wechat-account.json"))
    except LocalConfigurationError as error:
        check(error.code, "blocked", error.message)
    except Exception:
        check("CONFIG_FILE", "blocked", "无法安全读取本机配置或档案。")

    app_id, offer_id = settings.WECHAT_APP_ID, settings.VIRTUAL_PAYMENT_OFFER_ID
    valid_ids = bool(APP_PATTERN.fullmatch(app_id) and OFFER_PATTERN.fullmatch(offer_id))
    check("ACCOUNT_IDENTIFIERS", "ready" if valid_ids else "missing",
          "账号标识结构有效。" if valid_ids else "请填写有效的 AppID 与 OfferID。")
    if profile is None:
        check("ACCOUNT_PROFILE", "missing", "尚未保存本机非秘密账号档案。")
        check("PLATFORM_CONFIRMATIONS", "pending", "尚未记录平台人工确认，不能假定已开通。")
    elif profile["appId"] != app_id or profile["offerId"] != offer_id:
        check("ACCOUNT_PROFILE", "blocked", "账号档案与实际运行配置不一致，请人工核对；禁止自动换账号。")
    else:
        check("ACCOUNT_PROFILE", "ready", "本机档案与实际运行账号一致，不包含秘密。")
        all_confirmed = all(profile["platformConfirmed"].values())
        check("PLATFORM_CONFIRMATIONS", "ready" if all_confirmed else "pending",
              "已记录用户对备案、虚拟支付、Apple 及成员的确认；仅人工状态。" if all_confirmed
              else "部分平台状态尚未人工确认；不作为真实渠道验收。")

    for name in SECRET_FIELDS:
        value = getattr(settings, name, "")
        status = "missing" if not value else "ready" if secret_valid(name, value) else "blocked"
        check("SECRET_" + name, status, "秘密已填写，结构有效；未验证平台真实性。" if status == "ready"
              else "缺少秘密，请在本机终端隐藏录入。" if status == "missing" else "秘密结构无效，请人工核对。")
    keys = ",".join(settings.STUDENT_SESSION_ENCRYPTION_KEYS)
    key_status = "missing" if not keys else "ready" if key_ring_valid(keys) else "blocked"
    check("SESSION_ENCRYPTION", key_status, "本地密钥往返验证通过；不自动轮换既有密钥。" if key_status == "ready"
          else "会话密钥缺失或无效；存在旧加密数据时须恢复原密钥。")
    envelope = all(secret_valid(name, getattr(settings, name, "")) for name in
                   ("VIRTUAL_PAYMENT_CALLBACK_TOKEN", "VIRTUAL_PAYMENT_CALLBACK_AES_KEY")) and bool(APP_PATTERN.fullmatch(app_id))
    check("PAYMENT_ENVELOPE", "ready" if envelope else "missing", "消息安全信封结构有效，未联网验证回调。" if envelope
          else "支付消息安全信封尚不完整。")
    disabled = all(getattr(settings, name, False) is False for name in SALES_FIELDS)
    check("SALES_DISABLED", "ready" if disabled else "blocked", "本轮三项新销售开关均关闭。" if disabled
          else "存在已开启销售开关；本工具不自动关闭，请人工核对。")
    check("PAYMENT_ENVIRONMENT", "ready" if settings.VIRTUAL_PAYMENT_ENV == 0 else "pending",
          "当前为正式环境配置；不代表已完成真实支付。" if settings.VIRTUAL_PAYMENT_ENV == 0
          else "Android 沙箱配置；Apple 支付不能使用沙箱环境。")

    config = settings.DATABASES["default"]
    values = dict(file_values)
    values["DB_ENGINE"] = "sqlite" if config["ENGINE"] == "django.db.backends.sqlite3" else "postgresql" if config["ENGINE"] == "django.db.backends.postgresql" else "unsupported"
    if values["DB_ENGINE"] == "sqlite":
        values["SQLITE_PATH"] = str(config["NAME"])
    else:
        for name in ("NAME", "USER", "PASSWORD", "HOST", "PORT"):
            values["DB_" + name] = str(config.get(name) or "")
    try:
        data = safe_probe(directory, values, details=True)
        check("DATABASE", "ready" if data.get("exists") else "missing", "已有数据库只读访问通过。" if data.get("exists") else "数据库尚未初始化；本检查不创建文件。")
        check("DATABASE_ENGINE", "ready", "当前使用本机 SQLite；不代表 PostgreSQL 交易并发验收。" if data["engine"] == "sqlite"
              else "当前使用 PostgreSQL；这里只验证只读访问，不执行并发交易。")
        if data.get("exists"):
            required = set(MigrationLoader(None).disk_migrations)
            migrated = required <= data["applied"]
            check("MIGRATIONS", "ready" if migrated else "missing", "已交付迁移均已执行。" if migrated else "存在未执行迁移，请先备份再 migrate。")
            check("ADMINISTRATOR", "ready" if data["admin"] else "missing", "存在有效后台管理员。" if data["admin"] else "没有有效管理员，请使用 createsuperuser；不提供默认密码。")
            groups = data["roles"]
            missing = any(not required <= groups.get(name, set()) for name, required in ROLES.items())
            excess = any(groups.get(name, set()) - required for name, required in ROLES.items())
            missing_content = any(not required <= groups.get(name, set()) for name, required in CONTENT_ROLES.items())
            status = "blocked" if excess else "missing" if missing or missing_content else "ready"
            check("BUSINESS_ROLES", status, "题库与业务分权组已初始化，业务组权限范围匹配。" if status == "ready"
                  else "业务组存在额外权限，须人工核对；初始化不会自动扩大或删改权限。" if status == "blocked"
                  else "题库或业务分权组缺少必要权限，请迁移后初始化并人工核对。")
            product = data["product"]
            ready_product = product is not None and bool(product["active"]) and type(product["price"]) is int and product["price"] > 0
            check("PRODUCT", "ready" if ready_product else "missing", "单商品已启用且价格为正；未验证平台价格。" if ready_product else "单商品未初始化、未启用或价格无效。")
            mapped = bool(product and product["mapping"])
            check("PRODUCT_MAPPING", "ready" if mapped else "missing", "平台商品映射已填写，仍需平台核对。" if mapped else "尚未填写平台商品映射。")
            synced = mapped and product["sync"] == "synced" and product["syncedPrice"] == product["price"]
            check("PRODUCT_SYNC", "ready" if synced else "pending", "后台保存了当前价格同步确认，实际平台仍须核对。" if synced else "商品尚未通过后台受权限的价格同步确认。")
            mismatch = bool(data["appIds"] - {app_id})
            check("ACCOUNT_DATA", "blocked" if mismatch else "ready", "已有身份、会话或交易关联其他应用；身份迁移不在本轮。" if mismatch else "已存身份及交易未发现应用不一致。")
            if data["encrypted"] and key_status != "ready":
                check("ENCRYPTION_KEY_RECOVERY_REQUIRED", "blocked", "已有加密数据但密钥不可用，必须恢复旧密钥，禁止自动生成替代。")
    except LocalConfigurationError as error:
        check(error.code, "blocked", error.message)
    except Exception:
        check("DATABASE_DIAGNOSIS", "blocked", "数据库结构暂不可检查；未输出数据库或异常详情。")

    try:
        from contracts.validation import load_document, validate_document
        valid_contract = not validate_document(load_document())["errors"]
        check("API_CONTRACT", "ready" if valid_contract else "blocked", "学员接口契约与路由校验通过。" if valid_contract else "接口契约校验不一致，请检查交付代码。")
    except Exception:
        check("API_CONTRACT", "blocked", "无法安全校验接口契约。")
    for code, message in EXTERNAL_CHECKS:
        check(code, "pending", message)
    return {"checks": checks}
