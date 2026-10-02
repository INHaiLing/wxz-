"""Wechat session keys use a separate, rotatable server encryption key ring."""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

from common.errors import BusinessError


def session_cipher():
    keys = getattr(settings, "STUDENT_SESSION_ENCRYPTION_KEYS", ())
    if not isinstance(keys, (tuple, list)) or not keys or len(keys) > 10:
        raise BusinessError("STUDENT_CONFIGURATION_ERROR", "学员安全配置不可用，请联系管理员。", 503)
    try:
        return MultiFernet([Fernet(key) for key in keys])
    except (TypeError, ValueError):
        raise BusinessError("STUDENT_CONFIGURATION_ERROR", "学员安全配置不可用，请联系管理员。", 503) from None


def encrypt_session_key(value):
    return session_cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_session_key(value):
    try:
        return session_cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, TypeError, ValueError):
        raise BusinessError("STUDENT_CONFIGURATION_ERROR", "微信会话密钥不可用，请重新登录或联系管理员。", 503) from None


def rotate_session_key(value):
    try:
        return session_cipher().rotate(value.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeError, TypeError, ValueError):
        raise BusinessError("STUDENT_CONFIGURATION_ERROR", "微信会话密钥轮换失败，请检查完整密钥配置。", 503) from None
