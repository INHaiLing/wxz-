"""Call business replay helpers inside a transaction after locking the User row."""
import hashlib
import json
import re

from .errors import BusinessError
from .models import IdempotencyRecord


def payload_digest(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", value):
        raise BusinessError("IDEMPOTENCY_KEY_REQUIRED", "请提供 8～128 位有效 Idempotency-Key。")
    return value


def lookup(user, operation, key, payload):
    key = require_key(key)
    record = IdempotencyRecord.objects.filter(user=user, operation=operation, key=key).first()
    if record and record.request_digest != payload_digest(payload):
        raise BusinessError("IDEMPOTENCY_CONFLICT", "同一请求标识不能用于不同内容。", 409)
    return record


def remember(user, operation, key, payload, response):
    return IdempotencyRecord.objects.create(
        user=user, operation=operation, key=require_key(key),
        request_digest=payload_digest(payload), response=response,
    )
