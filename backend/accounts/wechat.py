"""Fixed-endpoint WeChat code2session adapter. No runtime fake identity path."""

import base64
import binascii
import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings

from common.errors import BusinessError


@dataclass(frozen=True)
class WeChatLogin:
    app_id: str
    openid: str
    session_key: str


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def exchange_code(code):
    app_id = getattr(settings, "WECHAT_APP_ID", "")
    app_secret = getattr(settings, "WECHAT_APP_SECRET", "")
    timeout = getattr(settings, "WECHAT_LOGIN_TIMEOUT_SECONDS", 5)
    if (
        not isinstance(app_id, str) or not app_id.strip()
        or not isinstance(app_secret, str) or not app_secret.strip()
        or not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 10
    ):
        raise BusinessError("STUDENT_CONFIGURATION_ERROR", "微信登录配置不可用，请联系管理员。", 503)
    parameters = urlencode({"appid": app_id, "secret": app_secret, "js_code": code, "grant_type": "authorization_code"})
    request = Request("https://api.weixin.qq.com/sns/jscode2session?" + parameters, headers={"Accept": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError("response too large")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid response")
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError):
        # Never expose exception strings: urllib errors may include the secret URL.
        raise BusinessError("WECHAT_UNAVAILABLE", "微信登录暂时不可用，请稍后重试。", 503) from None
    error_code = payload.get("errcode", 0)
    if error_code in (40029, 40163):
        raise BusinessError("WECHAT_CODE_INVALID", "微信登录凭证已失效，请重新登录。", 401)
    if error_code != 0:
        raise BusinessError("WECHAT_UNAVAILABLE", "微信登录暂时不可用，请稍后重试。", 503)
    openid = payload.get("openid")
    session_key = payload.get("session_key")
    if not isinstance(openid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", openid):
        raise BusinessError("WECHAT_UNAVAILABLE", "微信登录响应不完整，请重新登录。", 503)
    try:
        if not isinstance(session_key, str) or not 1 <= len(session_key) <= 1024:
            raise ValueError("invalid key")
        decoded = base64.b64decode(session_key, validate=True)
        if not decoded:
            raise ValueError("empty key")
    except (binascii.Error, ValueError):
        raise BusinessError("WECHAT_UNAVAILABLE", "微信登录响应不完整，请重新登录。", 503) from None
    return WeChatLogin(app_id=app_id, openid=openid, session_key=session_key)
