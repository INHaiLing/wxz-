"""Fixed WeChat HTTPS adapters, bounded responses, and encrypted DB tokens."""
import json
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
        if "errcode" in result and (type(result["errcode"]) is not int or result["errcode"] != 0):
            raise ValueError()
        return result
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
    if not isinstance(token, str) or not token or len(token) > 4096 or type(seconds) is not int or not 120 < seconds <= 7200:
        raise BusinessError("PLATFORM_UNAVAILABLE", "微信接口令牌暂时不可用。", 503)
    cache.ciphertext = encrypt_session_key(token)
    cache.expires_at = timezone.now() + timedelta(seconds=seconds - 120)
    cache.save(_service=True)
    return token


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
    query = {"access_token": access_token(order.app_id), "pay_sig": pay_signature("/xpay/query_order", body, synchronization_key(order))}
    response = official_post("/xpay/query_order", body, query)
    if response.get("errcode") != 0 or not isinstance(response.get("order"), dict):
        raise BusinessError("PLATFORM_UNAVAILABLE", "微信查单结果无效。", 503)
    return response["order"]


@sensitive_variables("body", "query")
def notify_goods(order):
    synchronization_key(order)
    body = json.dumps({"order_id": order.pk, "env": order.environment}, separators=(",", ":"))
    query = {"access_token": access_token(order.app_id)}
    official_post("/xpay/notify_provide_goods", body, query, allow_empty=True)
