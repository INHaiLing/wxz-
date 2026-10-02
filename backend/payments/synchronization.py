"""One transactional state machine for trusted callbacks and cash-order queries."""
from django.db import IntegrityError, transaction
from django.utils import timezone

from common import idempotency
from common.errors import BusinessError
from entitlements.models import EntitlementSource
from entitlements.services import (
    _audit, entitlement_snapshot, grant_entitlement, has_active_entitlement,
    lock_reservation, lock_user, release_opening, revoke_payment_entitlement,
)
from . import gateway
from .models import Order, PaymentEvent, PaymentTask
from .protocol import invalid, number_field, text_field
from .services import order_payload


def _reference(order_id):
    try:
        return Order.objects.select_related("identity").get(pk=order_id)
    except Order.DoesNotExist:
        raise BusinessError("NOT_FOUND", "平台订单不存在。", 404) from None


def _task(order, kind):
    existing = PaymentTask.objects.select_for_update().filter(order=order, kind=kind).first()
    if existing is None:
        existing = PaymentTask(order=order, kind=kind)
        existing.save(_service=True)
    return existing


def _review(order, code):
    if order.status not in ("refunded", "closed"):
        order.status = "review"
    order.review_reason = code
    order.save(_service=True)


def _ids(order, platform_id, transaction_id):
    original = (order.platform_order_id, order.transaction_id)

    def conflict():
        order.platform_order_id, order.transaction_id = original
        return False

    for field, value in (("platform_order_id", platform_id), ("transaction_id", transaction_id)):
        if not value:
            continue
        previous = getattr(order, field)
        if previous and previous != value:
            return conflict()
        if Order.objects.exclude(pk=order.pk).filter(**{field: value}).exists():
            return conflict()
        setattr(order, field, value)
    try:
        with transaction.atomic():
            order.save(_service=True)
    except IntegrityError:
        return conflict()
    return True


def _paid(order, *, query=False):
    if order.status == "refunded":
        return "ignored", ""
    if order.status == "closed":
        _review(order, "LATE_PAID_AFTER_CLOSED")
        return "review", "LATE_PAID_AFTER_CLOSED"
    if not order.prepared_at or not order.sign_data or not order.configuration_digest:
        _review(order, "UNPREPARED_PAYMENT_REVIEW")
        return "review", "UNPREPARED_PAYMENT_REVIEW"
    if not order.user.is_active or order.user.is_staff or order.user.is_superuser:
        _review(order, "PAID_ACCOUNT_REVIEW")
        return "review", "PAID_ACCOUNT_REVIEW"
    source = EntitlementSource.objects.filter(source_type="payment", source_id=order.pk).first()
    if source is None and has_active_entitlement(order.user, order.scope):
        first_review = order.review_reason != "PAID_ENTITLEMENT_CONFLICT"
        _review(order, "PAID_ENTITLEMENT_CONFLICT")
        if first_review:
            _audit("payment_review", user=order.user, order_id=order.pk, code="PAID_ENTITLEMENT_CONFLICT")
        return "review", "PAID_ENTITLEMENT_CONFLICT"
    source = grant_entitlement(order.user, "payment", order.pk, scope=order.scope)
    if source.status == "revoked":
        return "ignored", ""
    order.status = "fulfilled"
    order.paid_at = order.paid_at or timezone.now()
    order.fulfilled_at = order.fulfilled_at or timezone.now()
    order.review_reason = ""
    order.save(_service=True)
    if query:
        _task(order, "deliver")
    return "applied", ""


def _refund(order, refund_id, amount):
    if amount != order.price_fen:
        _review(order, "REFUND_AMOUNT_REVIEW")
        return "review", "REFUND_AMOUNT_REVIEW"
    if order.status != "refunded":
        order.status = "refunded"
        order.refunded_at = timezone.now()
        order.review_reason = ""
        order.save(_service=True)
        _audit("payment_order_refunded", user=order.user, order_id=order.pk, refundId=refund_id)
    # Irreversible order state is committed with source revoke. If refund arrives
    # before payment, no source exists and late payment cannot create one.
    source = EntitlementSource.objects.filter(source_type="payment", source_id=order.pk).first()
    if source:
        revoke_payment_entitlement(source.pk, refund_id)
    # The verified final refund also completes this exact purchase reservation.
    reservation = lock_reservation(order.user, order.scope)
    if reservation and reservation.is_active and reservation.order_id == order.pk:
        reservation.released_at = timezone.now()
        reservation.save(_service=True)
        _audit("opening_refunded", user=order.user, order_id=order.pk, refundId=refund_id)
    return "applied", ""


def _record(order, kind, external_id, payload, operation):
    digest = idempotency.payload_digest(payload)
    key = idempotency.payload_digest({"kind": kind, "external": external_id})
    previous = PaymentEvent.objects.filter(pk=key).first()
    if previous:
        if previous.order_id != order.pk or previous.request_digest != digest:
            raise invalid()
        if previous.outcome == "review":
            outcome, error = operation()
            if outcome != "review":
                previous.outcome = outcome
                previous.error_code = error
                previous.details = {**previous.details, "initialOutcome": "review"}
                previous.save(_service=True)
                _audit("payment_event_resolved", user=order.user, order_id=order.pk, eventId=key)
        return previous
    outcome, error = operation()
    event = PaymentEvent(key=key, order=order, kind=kind, external_id=external_id,
                         request_digest=digest, outcome=outcome, error_code=error,
                         details=payload)
    event.save(_service=True)
    return event


@transaction.atomic
def apply_callback(data, app_id):
    kind = text_field(data, "Event", maximum=64)
    if data.get("MsgType") != "event":
        raise invalid()
    number_field(data, "CreateTime")
    if kind == "xpay_subscribe_ios_refund_query_notify":
        event_id = idempotency.payload_digest(data)
        event = PaymentEvent.objects.filter(pk=event_id).first()
        if event is None:
            PaymentEvent(key=event_id, kind=kind, request_digest=event_id, outcome="uncertain", details={"policy": "platform_uncertain"}).save(_service=True)
        return "uncertain"
    if kind not in ("xpay_goods_deliver_notify", "xpay_refund_notify"):
        raise invalid()
    reference = _reference(text_field(data, "OutTradeNo" if kind == "xpay_goods_deliver_notify" else "MchOrderId", maximum=32))
    user = lock_user(reference.user_id)
    lock_reservation(user)
    order = Order.objects.select_for_update().select_related("identity", "user").get(pk=reference.pk)
    if order.app_id != app_id or text_field(data, "OpenId") != order.identity.openid:
        raise invalid()
    if kind == "xpay_goods_deliver_notify":
        if number_field(data, "Env") != order.environment:
            raise invalid()
        goods = data.get("GoodsInfo")
        if not isinstance(goods, dict) or text_field(goods, "ProductId") != order.platform_product_id or number_field(goods, "Quantity") != 1 or number_field(goods, "OrigPrice") != order.price_fen or number_field(goods, "ActualPrice") != order.price_fen or text_field(goods, "Attach", maximum=32) != order.pk:
            raise invalid()
        pay = data.get("WeChatPayInfo", {})
        if not isinstance(pay, dict):
            raise invalid()
        transaction_id = text_field(pay, "TransactionId", optional=True)
        if pay:
            number_field(pay, "PaidTime")
        payload = {"orderId": order.pk, "priceFen": order.price_fen, "transactionId": transaction_id, "environment": order.environment}

        def paid():
            if not _ids(order, "", transaction_id):
                _review(order, "PLATFORM_ID_CONFLICT")
                return "review", "PLATFORM_ID_CONFLICT"
            return _paid(order)
        event = _record(order, kind, order.pk, payload, paid)
    else:
        refund_id = text_field(data, "WxRefundId")
        text_field(data, "MchRefundId")
        platform_id = text_field(data, "WxOrderId")
        transaction_id = text_field(data, "WxTransactionId", optional=True)
        attach = text_field(data, "Attach", optional=True, maximum=32)
        if attach and attach != order.pk:
            raise invalid()
        amount = number_field(data, "RefundFee", minimum=1)
        result = number_field(data, "RetCode", minimum=-2147483648)
        started = number_field(data, "RefundStartTimestamp")
        ended = number_field(data, "RefundSuccTimestamp")
        if result == 0 and (not ended or ended < started):
            raise invalid()
        payload = {"orderId": order.pk, "refundId": refund_id, "refundFee": amount, "retCode": result, "platformOrderId": platform_id, "transactionId": transaction_id}

        def refunded():
            if not _ids(order, platform_id, transaction_id):
                _review(order, "PLATFORM_ID_CONFLICT")
                return "review", "PLATFORM_ID_CONFLICT"
            return _refund(order, refund_id, amount) if result == 0 else ("ignored", "")
        event = _record(order, kind, refund_id + ":" + str(result), payload, refunded)
    return event.outcome


@transaction.atomic
def apply_query(order_id, snapshot):
    reference = _reference(order_id)
    user = lock_user(reference.user_id)
    lock_reservation(user)
    order = Order.objects.select_for_update().select_related("identity", "user").get(pk=order_id)
    if text_field(snapshot, "order_id", maximum=32) != order.pk or number_field(snapshot, "env_type") != order.environment + 1:
        raise invalid()
    status = number_field(snapshot, "status")
    order_type = number_field(snapshot, "order_type")
    if order_type != (7 if order.channel == "ios" else 0):
        raise invalid()
    platform_id = text_field(snapshot, "wx_order_id", optional=status not in (2, 3, 4, 5, 8))
    transaction_id = text_field(snapshot, "wxpay_order_id", optional=True)
    if status in (2, 3, 4, 5, 8) and number_field(snapshot, "order_fee", minimum=1) != order.price_fen:
        raise invalid()
    if status in (2, 3, 4) and number_field(snapshot, "paid_fee", minimum=1) != order.price_fen:
        raise invalid()
    metadata = snapshot.get("biz_meta", "")
    if metadata and metadata not in (order.pk, order.sign_data):
        raise invalid()
    payload = {"orderId": order.pk, "status": status, "platformOrderId": platform_id,
               "transactionId": transaction_id, "orderFee": snapshot.get("order_fee"),
               "leftFee": snapshot.get("left_fee"), "updateTime": snapshot.get("update_time")}
    digest = idempotency.payload_digest(payload)

    def synchronize():
        if not _ids(order, platform_id, transaction_id):
            _review(order, "PLATFORM_ID_CONFLICT")
            return "review", "PLATFORM_ID_CONFLICT"
        order.last_platform_status = status
        order.save(_service=True)
        if status in (5, 8):
            left = number_field(snapshot, "left_fee")
            return _refund(order, "query:" + digest, order.price_fen - left)
        if status in (2, 3, 4):
            return _paid(order, query=True)
        if status == 6 and order.status == "preparing":
            order.status = "closed"
            order.save(_service=True)
            release_opening(user, order.pk, verified_final_unpaid=True)
            return "applied", ""
        return "ignored", ""
    _record(order, "query", digest, payload, synchronize)
    order.refresh_from_db()
    return {"order": order_payload(order), "entitlement": entitlement_snapshot(user)}


def query_payment(user, order_id, key):
    # Reserve the idempotency identity under the same User mutex, but do not keep
    # the transaction open while waiting on the external platform.
    with transaction.atomic():
        locked = lock_user(user)
        lock_reservation(locked)
        if not locked.is_active or locked.is_staff or locked.is_superuser:
            raise BusinessError("AUTH_REQUIRED", "学员账户不可用。", 401)
        order = Order.objects.select_for_update().filter(pk=order_id, user=locked).first()
        if order is None:
            raise BusinessError("NOT_FOUND", "订单不存在。", 404)
        previous = idempotency.lookup(locked, "payment-query", key, {"orderId": order.pk})
        if previous:
            return {"order": order_payload(order), "entitlement": entitlement_snapshot(locked)}
        if order.status == "created":
            raise BusinessError("PAYMENT_NOT_PREPARED", "订单尚未进入支付，请先获取支付参数。", 409)
    snapshot = gateway.query_order(order)
    result = apply_query(order.pk, snapshot)
    with transaction.atomic():
        locked = lock_user(user)
        lock_reservation(locked)
        if not idempotency.lookup(locked, "payment-query", key, {"orderId": order.pk}):
            idempotency.remember(locked, "payment-query", key, {"orderId": order.pk}, {"orderId": order.pk})
    return result
