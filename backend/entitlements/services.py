"""Use the existing User row as the common mutex for all opening operations."""

import hashlib
import json
import re

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from common.errors import BusinessError
from .models import SCOPE, AuditEvent, EntitlementSource, OpeningReservation, Product


def _scope(scope):
    if scope != SCOPE:
        raise BusinessError("SCOPE_NOT_SUPPORTED", "当前只支持全语文题库权益。")
    return scope


def _business_id(value):
    value = str(value) if value is not None else ""
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
        raise BusinessError("INVALID_SOURCE_ID", "业务编号格式无效。")
    return value


def _require_admin(actor, permission):
    if not (actor and actor.is_authenticated and actor.is_active and actor.is_staff and actor.has_perm(permission)):
        raise BusinessError("FORBIDDEN", "当前账户没有此操作权限。", 403)


def lock_user(user_or_id):
    if not connection.in_atomic_block:
        raise RuntimeError("lock_user must run inside transaction.atomic().")
    user_id = getattr(user_or_id, "pk", user_or_id)
    try:
        return get_user_model().objects.select_for_update().get(pk=user_id)
    except get_user_model().DoesNotExist as error:
        raise BusinessError("NOT_FOUND", "账户不存在。", 404) from error


def lock_reservation(user, scope=SCOPE):
    """Call only after lock_user; includes released row for safe reuse."""
    _scope(scope)
    return OpeningReservation.objects.select_for_update().filter(user=user, scope=scope).first()


def has_active_entitlement(user, scope=SCOPE):
    _scope(scope)
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return EntitlementSource.objects.filter(user=user, scope=scope, status="active").exists()


def entitlement_snapshot(user, scope=SCOPE):
    active = has_active_entitlement(user, scope)
    return {"scope": scope, "active": active, "permanent": active, "expiresAt": None}


def _audit(kind, *, user=None, actor=None, source=None, order_id="", **details):
    event = AuditEvent(kind=kind, user=user, actor=actor, source=source, order_id=order_id, details=details)
    event.save(_service=True)
    return event


def _check_new_opening(locked_user, reservation):
    if not locked_user.is_active:
        raise BusinessError("AUTH_REQUIRED", "账户已停用。", 401)
    if has_active_entitlement(locked_user):
        raise BusinessError("ALREADY_ACTIVATED", "已激活", 409)
    if reservation and reservation.is_active:
        raise BusinessError("PURCHASE_IN_PROGRESS", "购买处理中，请等待平台确认。", 409)


@transaction.atomic
def check_new_opening(user, scope=SCOPE):
    _scope(scope)
    locked = lock_user(user)
    _check_new_opening(locked, lock_reservation(locked, scope))
    return locked


@transaction.atomic
def reserve_opening(user, order_id, scope=SCOPE):
    _scope(scope)
    order_id = _business_id(order_id)
    locked = lock_user(user)
    reservation = lock_reservation(locked, scope)
    if not locked.is_active:
        raise BusinessError("AUTH_REQUIRED", "账户已停用。", 401)
    if has_active_entitlement(locked):
        raise BusinessError("ALREADY_ACTIVATED", "已激活", 409)
    if reservation and reservation.is_active:
        if reservation.order_id == order_id:
            return reservation
        raise BusinessError("PURCHASE_IN_PROGRESS", "购买处理中，请等待平台确认。", 409)
    reservation = reservation or OpeningReservation(user=locked, scope=scope)
    reservation.order_id = order_id
    reservation.reserved_at = timezone.now()
    reservation.released_at = None
    reservation.release_reason = ""
    reservation.save(_service=True)
    _audit("opening_reserved", user=locked, order_id=order_id, scope=scope)
    return reservation


def _release_reservation(reservation, *, reason):
    reservation.released_at = timezone.now()
    reservation.release_reason = reason
    reservation.save(_service=True)
    _audit("opening_released", user=reservation.user, order_id=reservation.order_id, scope=reservation.scope, reason=reason)


@transaction.atomic
def release_opening(user, order_id, *, verified_final_unpaid=False, scope=SCOPE):
    _scope(scope)
    if verified_final_unpaid is not True:
        raise BusinessError("UNVERIFIED_RELEASE", "只有平台确认最终未付款，才允许解除支付占位。", 409)
    order_id = _business_id(order_id)
    locked = lock_user(user)
    reservation = lock_reservation(locked, scope)
    if reservation is None:
        return None
    if reservation.order_id != order_id:
        raise BusinessError("RESERVATION_CONFLICT", "该请求不能解除其他订单的支付占位。", 409)
    if reservation.is_active:
        _release_reservation(reservation, reason="verified_final_unpaid")
    return reservation


@transaction.atomic
def grant_entitlement(user, source_type, source_id, actor=None, scope=SCOPE):
    _scope(scope)
    if source_type not in ("activation", "payment"):
        raise BusinessError("INVALID_SOURCE_TYPE", "权益来源类型无效。")
    source_id = _business_id(source_id)
    if actor is not None:
        _require_admin(actor, "entitlements.grant_entitlementsource")
    locked = lock_user(user)
    reservation = lock_reservation(locked, scope)
    source = EntitlementSource.objects.select_for_update().filter(source_type=source_type, source_id=source_id).first()
    if source is None:
        source = EntitlementSource(user=locked, scope=scope, source_type=source_type, source_id=source_id, created_by=actor)
        try:
            # Another user's lock does not serialize a conflicting source ID.
            # Let the unique constraint decide, then inspect after savepoint rollback.
            with transaction.atomic():
                source.save(_service=True)
        except IntegrityError:
            source = EntitlementSource.objects.select_for_update().get(source_type=source_type, source_id=source_id)
        else:
            _audit("granted", user=locked, actor=actor, source=source, scope=scope, sourceType=source_type, sourceId=source_id)
    if source.user_id != locked.pk or source.scope != scope:
        raise BusinessError("SOURCE_CONFLICT", "该业务来源已经关联其他账户或权益。", 409)
    if source.status == "active" and source_type == "payment" and reservation and reservation.is_active and reservation.order_id == source_id:
        _release_reservation(reservation, reason="fulfilled")
    return source


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def revocation_preview(source):
    sources = list(EntitlementSource.objects.filter(user_id=source.user_id, scope=source.scope).order_by("id"))
    others = [item for item in sources if item.pk != source.pk and item.status == "active"]
    fingerprint = _digest({
        "target": str(source.pk), "user": source.user_id, "scope": source.scope,
        "sources": [{"id": str(item.pk), "type": item.source_type, "sourceId": item.source_id, "status": item.status, "revokedAt": item.revoked_at} for item in sources],
    })
    return {"fingerprint": fingerprint, "remainingSourceCount": len(others), "willRemainActive": bool(others)}


def _lock_source_owner(source_id):
    try:
        reference = EntitlementSource.objects.get(pk=source_id)
    except (EntitlementSource.DoesNotExist, ValueError, TypeError, ValidationError) as error:
        raise BusinessError("NOT_FOUND", "权益来源不存在。", 404) from error
    user = lock_user(reference.user_id)
    lock_reservation(user, reference.scope)
    return user, EntitlementSource.objects.select_for_update().get(pk=source_id)


def _revoke_source(user, source, *, actor, reason, kind="revoked", **details):
    if source.status == "revoked":
        return source
    preview = revocation_preview(source)
    source.status = "revoked"
    source.revoked_at = timezone.now()
    source.revoked_by = actor
    source.reason = reason
    source.save(_service=True)
    _audit(kind, user=user, actor=actor, source=source, reason=reason,
           remainingSourceCount=preview["remainingSourceCount"],
           willRemainActive=preview["willRemainActive"], **details)
    return source


@transaction.atomic
def revoke_entitlement(source_id, actor, reason, expected_fingerprint=None):
    _require_admin(actor, "entitlements.revoke_entitlementsource")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        raise BusinessError("REASON_REQUIRED", "请填写 1～500 字撤销理由。")
    user, source = _lock_source_owner(source_id)
    preview = revocation_preview(source)
    if expected_fingerprint is not None and expected_fingerprint != preview["fingerprint"]:
        raise BusinessError("VERSION_CONFLICT", "权益状态或撤销影响已变化，请重新预览确认。", 409)
    return _revoke_source(user, source, actor=actor, reason=reason.strip())


@transaction.atomic
def revoke_payment_entitlement(source_id, refund_id):
    """Trusted internal primitive, only after P10 verifies a completed refund.

    The order must first enter its irreversible refunded state; an absent source
    is handled by the order workflow, never by granting then revoking here.
    """
    refund_id = _business_id(refund_id)
    user, source = _lock_source_owner(source_id)
    if source.source_type != "payment":
        raise BusinessError("SOURCE_CONFLICT", "退款同步只能撤销付款来源。", 409)
    return _revoke_source(user, source, actor=None, reason="平台退款：" + refund_id,
                          kind="payment_refunded", refundId=refund_id)


def product_fingerprint(product):
    return _digest({"id": product.pk, "price": product.price_fen, "mapping": product.platform_product_id, "sync": product.platform_sync_state, "updatedAt": product.updated_at})


@transaction.atomic
def mark_product_synced(product_id, actor, *, price_fen, platform_product_id, expected_fingerprint=None):
    _require_admin(actor, "entitlements.sync_product")
    product = Product.objects.select_for_update().get(pk=product_id)
    if expected_fingerprint is not None and product_fingerprint(product) != expected_fingerprint:
        raise BusinessError("VERSION_CONFLICT", "商品配置已变化，请重新确认。", 409)
    if product.price_fen != price_fen or product.platform_product_id != platform_product_id or not platform_product_id:
        raise BusinessError("PRODUCT_SYNC_MISMATCH", "请先配置平台商品编号，并核对平台金额与本地金额一致。", 409)
    if product.platform_sync_state == "synced" and product.platform_synced_price_fen == price_fen:
        return product
    product.platform_sync_state = "synced"
    product.platform_synced_price_fen = price_fen
    product.platform_synced_at = timezone.now()
    product.save(_sync=True)
    _audit("product_synced", actor=actor, productId=product.pk, priceFen=price_fen, platformProductId=platform_product_id)
    return product
