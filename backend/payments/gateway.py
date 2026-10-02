"""Fixed WeChat HTTPS adapters, bounded responses, and encrypted DB tokens."""
import json
import hmac
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from accounts.crypto import decrypt_session_key, encrypt_session_key
from common.errors import BusinessError
from .models import AccessTokenCache
from .protocol import MAX_BYTES, parse_payload
from .signing import pay_signature


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PlatformRequestFailure(BusinessError):
    """A numeric control signal that never enters the public error payload."""

    def __init__(self, platform_code=None):
        self.platform_code = platform_code
        super().__init__("PLATFORM_UNAVAILABLE", "微信平台暂时不可用，请稍后重试。", 503)


TOKEN_FAILURE_CODES = frozenset((40001, 40014, 42001))


@sensitive_variables("body", "raw", "url", "request", "query", "result")
def official_post(path, body, query=None, *, allow_empty=False):
    if path not in ("/cgi-bin/stable_token", "/xpay/query_order", "/xpay/notify_provide_goods"):
        raise ValueError("unsupported official endpoint")
    url = "https://api.weixin.qq.com" + path + ("?" + urlencode(query) if query else "")
    request = Request(url, data=body.encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with build_opener(NoRedirect()).open(request, timeout=5) as response:
            if response.status != 200:
                raise ValueError()
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError()
        if not raw and allow_empty:
            return {}
        if not raw.lstrip().startswith(b"{"):
            raise ValueError()
        result = parse_payload(raw)
        if "errcode" in result:
            if type(result["errcode"]) is not int:
                raise ValueError()
            if result["errcode"] != 0:
                raise PlatformRequestFailure(result["errcode"])
        return result
    except PlatformRequestFailure:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, BusinessError):
        raise BusinessError("PLATFORM_UNAVAILABLE", "微信平台暂时不可用，请稍后重试。", 503) from None


@sensitive_variables("secret", "body", "response", "token")
@transaction.atomic
def access_token(app_id):
    if not app_id or app_id != getattr(settings, "WECHAT_APP_ID", ""):
        raise BusinessError("PAYMENT_CONFIGURATION_ERROR", "微信身份配置不可用。", 503)
    secret = getattr(settings, "WECHAT_APP_SECRET", "")
    if not isinstance(secret, str) or not secret:
        raise BusinessError("PAYMENT_CONFIGURATION_ERROR", "微信接口配置不可用。", 503)
    cache = AccessTokenCache.objects.filter(pk=app_id).first()
    if cache is None:
        try:
            with transaction.atomic():
                AccessTokenCache(app_id=app_id).save(_service=True)
        except IntegrityError:
            pass
    cache = AccessTokenCache.objects.select_for_update().get(pk=app_id)
    if cache.ciphertext and cache.expires_at and cache.expires_at > timezone.now():
        return decrypt_session_key(cache.ciphertext)
    body = json.dumps({"grant_type": "client_credential", "appid": app_id, "secret": secret, "force_refresh": False}, separators=(",", ":"))
    response = official_post("/cgi-bin/stable_token", body)
    token = response.get("access_token")
    seconds = response.get("expires_in")
    if not isinstance(token, str) or not token or len(token) > 4096 or type(seconds) is not int or not 0 < seconds <= 7200:
        raise BusinessError("PLATFORM_UNAVAILABLE", "微信接口令牌暂时不可用。", 503)
    cache.ciphertext = encrypt_session_key(token)
    # Stable tokens may have only a short positive lifetime left. Preserve an
    # early refresh margin without making a valid short token instantly expire.
    cache.expires_at = timezone.now() + timedelta(seconds=seconds - min(120, seconds / 2))
    cache.save(_service=True)
    return token


@sensitive_variables("rejected_token", "current_token", "cache")
@transaction.atomic
def invalidate_cached_token(app_id, rejected_token):
    cache = AccessTokenCache.objects.select_for_update().filter(pk=app_id).first()
    if cache is None or not cache.ciphertext:
        return False
    current_token = decrypt_session_key(cache.ciphertext)
    if not hmac.compare_digest(current_token.encode(), rejected_token.encode()):
        return False  # A concurrent request already cached a newer token.
    cache.ciphertext = ""
    cache.expires_at = None
    cache.save(_service=True)
    return True


@sensitive_variables("body", "query", "used_token")
def xpay_post(path, body, app_id, query=None, *, allow_empty=False):
    if path not in ("/xpay/query_order", "/xpay/notify_provide_goods"):
        raise ValueError("unsupported xpay endpoint")
    used_token = access_token(app_id)
    query = {**(query or {}), "access_token": used_token}
    try:
        return official_post(path, body, query, allow_empty=allow_empty)
    except PlatformRequestFailure as error:
        if error.platform_code not in TOKEN_FAILURE_CODES:
            raise
    invalidate_cached_token(app_id, used_token)
    query = {**query, "access_token": access_token(app_id)}
    # One recovery only. A second rejection or token-endpoint error propagates
    # as the same safe BusinessError; there is no recursive refresh or retry.
    return official_post(path, body, query, allow_empty=allow_empty)


def synchronization_key(order):
    # Existing payments must keep syncing even when new sales are disabled.
    if order.app_id != getattr(settings, "WECHAT_APP_ID", ""):
        raise BusinessError("PAYMENT_CONFIGURATION_CHANGED", "支付应用配置已变化。", 503)
    key = getattr(settings, "VIRTUAL_PAYMENT_APP_KEY" if order.environment == 0 else "VIRTUAL_PAYMENT_SANDBOX_APP_KEY", "")
    if not isinstance(key, str) or not key:
        raise BusinessError("PAYMENT_CONFIGURATION_ERROR", "支付核验配置不可用。", 503)
    return key


@sensitive_variables("body", "query")
def query_order(order):
    body = json.dumps({"openid": order.identity.openid, "env": order.environment, "order_id": order.pk}, separators=(",", ":"))
    query = {"pay_sig": pay_signature("/xpay/query_order", body, synchronization_key(order))}
    response = xpay_post("/xpay/query_order", body, order.app_id, query)
    if response.get("errcode") != 0 or not isinstance(response.get("order"), dict):
        raise BusinessError("PLATFORM_UNAVAILABLE", "微信查单结果无效。", 503)
    return response["order"]


@sensitive_variables("body", "query")
def notify_goods(order):
    synchronization_key(order)
    body = json.dumps({"order_id": order.pk, "env": order.environment}, separators=(",", ":"))
    xpay_post("/xpay/notify_provide_goods", body, order.app_id, allow_empty=True)
