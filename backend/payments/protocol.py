"""WeChat safe-mode envelopes. No business state is accepted without AES auth."""
import base64
import binascii
import hashlib
import hmac
import json
import re
import struct
from xml.etree import ElementTree

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from common.errors import BusinessError

MAX_BYTES = 65536


def invalid():
    return BusinessError("INVALID_PLATFORM_MESSAGE", "平台消息校验失败。", 400)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise invalid()
        value[key] = item
    return value


def parse_payload(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise invalid()
    try:
        text = raw.decode("utf-8")
        if text.lstrip().startswith("{"):
            value = json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(invalid()))
        else:
            if "<!" in text.upper().replace("<![CDATA[", ""):
                raise invalid()
            root = ElementTree.fromstring(text)
            if root.tag != "xml" or root.attrib:
                raise invalid()

            def convert(node, depth=0):
                if depth > 8 or node.attrib:
                    raise invalid()
                if not len(node):
                    return node.text or ""
                if node.text and node.text.strip():
                    raise invalid()
                return _pairs((child.tag, convert(child, depth + 1)) for child in node)
            value = convert(root)
        if not isinstance(value, dict):
            raise invalid()
        return value
    except (UnicodeError, ValueError, RecursionError, ElementTree.ParseError):
        raise invalid() from None


def callback_configuration():
    token = getattr(settings, "VIRTUAL_PAYMENT_CALLBACK_TOKEN", "")
    encoded = getattr(settings, "VIRTUAL_PAYMENT_CALLBACK_AES_KEY", "")
    app_id = getattr(settings, "WECHAT_APP_ID", "")
    try:
        if not isinstance(token, str) or not token or not isinstance(encoded, str) or len(encoded) != 43 or not app_id:
            raise ValueError()
        key = base64.b64decode(encoded + "=", validate=True)
        if len(key) != 32:
            raise ValueError()
    except (ValueError, binascii.Error):
        raise BusinessError("PAYMENT_CONFIGURATION_ERROR", "支付消息安全配置不可用。", 503) from None
    return token, key, app_id


def message_signature(token, timestamp, nonce, encrypted=None):
    parts = [token, timestamp, nonce] + ([encrypted] if encrypted is not None else [])
    return hashlib.sha1("".join(sorted(parts)).encode("utf-8")).hexdigest()


def verify_signature(token, query, encrypted=None):
    timestamp = query.get("timestamp", "")
    nonce = query.get("nonce", "")
    signature = query.get("msg_signature" if encrypted is not None else "signature", "")
    if not re.fullmatch(r"[0-9]{1,12}", timestamp) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", nonce) or not re.fullmatch(r"[0-9a-f]{40}", signature):
        raise invalid()
    if not hmac.compare_digest(signature, message_signature(token, timestamp, nonce, encrypted)):
        raise invalid()


@sensitive_variables("key", "clear", "ciphertext")
def decrypt_message(encrypted, key, app_id):
    try:
        if not isinstance(encrypted, str) or len(encrypted) > MAX_BYTES:
            raise ValueError()
        ciphertext = base64.b64decode(encrypted, validate=True)
        if not ciphertext or len(ciphertext) % 16:
            raise ValueError()
        decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
        clear = decryptor.update(ciphertext) + decryptor.finalize()
        padding = clear[-1]
        if not 1 <= padding <= 32 or clear[-padding:] != bytes([padding]) * padding:
            raise ValueError()
        clear = clear[:-padding]
        if len(clear) < 20:
            raise ValueError()
        length = struct.unpack("!I", clear[16:20])[0]
        if length > MAX_BYTES or 20 + length > len(clear) or clear[20 + length:] != app_id.encode():
            raise ValueError()
        return clear[20:20 + length]
    except (ValueError, TypeError, binascii.Error):
        raise invalid() from None


def text_field(data, name, *, optional=False, maximum=128):
    value = data.get(name, "")
    if optional and value == "":
        return ""
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise invalid()
    return value


def number_field(data, name, *, minimum=0):
    value = data.get(name)
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,15}", value):
        value = int(value)
    if type(value) is not int or value < minimum or value > 10**15:
        raise invalid()
    return value
