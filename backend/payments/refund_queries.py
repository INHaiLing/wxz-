"""Bounded Apple inquiry audit; no refund recommendation or entitlement writes."""
import hashlib
import json
import re

from django.db import IntegrityError, transaction
from django.db.models import Q

from common.idempotency import payload_digest
from .models import Order, PaymentEvent
from .protocol import invalid

KIND = "xpay_subscribe_ios_refund_query_notify"
FIELD_LIMITS = {
    "refund_time": 15, "order_time": 15, "channel_bill": 4096,
    "bundleid": 255, "product_id": 128, "p_count": 9,
    "refund_request_reason": 256, "provide_status": 1, "pay_order_id": 128,
}


def _information(data):
    info, missing, malformed = {}, [], []
    for field, maximum in FIELD_LIMITS.items():
        if field not in data:
            missing.append(field)
            continue
        value = data[field]
        valid = isinstance(value, str) and 0 < len(value) <= maximum and not any(
            ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value
        )
        if valid and field in ("refund_time", "order_time", "p_count"):
            valid = bool(re.fullmatch(r"[0-9]+", value)) and (field != "p_count" or int(value) > 0)
        if valid and field == "provide_status":
            valid = value in ("0", "1", "2")
        if not valid:
            malformed.append(field)
        elif field == "channel_bill":
            # The complete Apple receipt never reaches DB, admin, or logs.
            info["channel_bill_digest"] = hashlib.sha256(value.encode()).hexdigest()
        else:
            info[field] = value
    return info, missing, malformed


def _association(info, malformed, app_id):
    if malformed:
        return None, "invalid_fields", "REFUND_QUERY_INVALID_FIELDS"
    if any(field not in info for field in ("pay_order_id", "product_id", "p_count")):
        return None, "insufficient_info", "REFUND_QUERY_INCOMPLETE"
    if int(info["p_count"]) != 1:
        return None, "quantity_mismatch", "REFUND_QUERY_QUANTITY_MISMATCH"
    reference = info["pay_order_id"]
    candidates = list(Order.objects.filter(Q(pk=reference) | Q(platform_order_id=reference))[:2])
    if not candidates:
        return None, "unknown_order", "REFUND_QUERY_UNKNOWN_ORDER"
    if len(candidates) != 1:
        return None, "ambiguous_order", "REFUND_QUERY_AMBIGUOUS_ORDER"
    order = candidates[0]
    if order.app_id != app_id or order.channel != "ios":
        return None, "order_mismatch", "REFUND_QUERY_ORDER_MISMATCH"
    if order.platform_product_id != info["product_id"]:
        return None, "product_mismatch", "REFUND_QUERY_PRODUCT_MISMATCH"
    return order, "linked", ""


@transaction.atomic
def record_refund_query(data, app_id):
    try:
        event_id = payload_digest(data)
    except UnicodeError:
        # Escaped unpaired surrogates are legal JSON syntax but unusable audit
        # text. Hash their escaped form, then record only the invalid field name.
        escaped = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        event_id = hashlib.sha256(escaped.encode("ascii")).hexdigest()
    previous = PaymentEvent.objects.filter(pk=event_id).first()
    if previous is not None:
        if previous.kind != KIND or previous.request_digest != event_id:
            raise invalid()
        return "uncertain"
    info, missing, malformed = _information(data)
    order, association, error_code = _association(info, malformed, app_id)
    event = PaymentEvent(key=event_id, order=order, kind=KIND,
                         external_id=info.get("pay_order_id", ""), request_digest=event_id,
                         outcome="uncertain", error_code=error_code,
                         details={"policy": "platform_uncertain", "info": info,
                                  "association": association, "missing_fields": missing,
                                  "invalid_fields": malformed})
    try:
        with transaction.atomic():
            event.save(_service=True)
    except IntegrityError:
        # PostgreSQL's competing insert has committed before this unique
        # violation. The savepoint keeps the enclosing callback transaction usable.
        previous = PaymentEvent.objects.filter(pk=event_id).first()
        if previous is None or previous.kind != KIND or previous.request_digest != event_id:
            raise
    return "uncertain"
