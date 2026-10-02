import base64
import hashlib
import re
import secrets

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from common import idempotency
from common.errors import BusinessError
from entitlements.services import (
    _audit, _require_admin, check_new_opening, entitlement_snapshot,
    grant_entitlement, lock_reservation, lock_user,
)
from .models import ActivationBatch, ActivationCode, ActivationRedemption


TRANSITIONS = {
    "receive": ("awaiting_receipt", "received", "receive_activationbatch"),
    "enable": (("received", "disabled"), "enabled", "enable_activationbatch"),
    "disable": ("enabled", "disabled", "disable_activationbatch"),
    "void": ("awaiting_receipt", "void", "void_activationbatch"),
}


@sensitive_variables("value", "canonical")
def code_digest(value):
    if not isinstance(value, str) or len(value) > 128:
        raise BusinessError("CODE_INVALID", "激活码格式无效，请核对后重试。")
    canonical = re.sub(r"[\s-]", "", value, flags=re.ASCII).upper()
    if not re.fullmatch(r"QM[A-Z2-7]{52}", canonical):
        raise BusinessError("CODE_INVALID", "激活码格式无效，请核对后重试。")
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


@sensitive_variables("raw", "encoded")
def _new_code():
    encoded = base64.b32encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    raw = "QM-" + "-".join(encoded[index:index + 4] for index in range(0, len(encoded), 4))
    return raw, code_digest(raw), "QM-" + encoded[:4] + "-…-" + encoded[-4:]


@sensitive_variables("raw_codes", "raw", "digest")
@transaction.atomic
def generate_batch(actor, quantity, label, key):
    _require_admin(actor, "activation.generate_activationbatch")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 500:
        raise BusinessError("INVALID_QUANTITY", "一次可生成 1～500 个激活码。")
    if not isinstance(label, str) or len(label) > 120:
        raise BusinessError("INVALID_LABEL", "用途说明最多 120 字。")
    actor = lock_user(actor)
    _require_admin(actor, "activation.generate_activationbatch")
    payload = {"quantity": quantity, "label": label.strip()}
    previous = idempotency.lookup(actor, "activation.generate", key, payload)
    if previous:
        return ActivationBatch.objects.get(pk=previous.response["batchId"]), None
    batch = ActivationBatch(quantity=quantity, label=label.strip(), created_by=actor)
    batch.save(_service=True)
    raw_codes = []
    for _ in range(quantity):
        raw, digest, mask = _new_code()
        ActivationCode(batch=batch, digest=digest, mask=mask).save(_service=True)
        raw_codes.append(raw)
    idempotency.remember(actor, "activation.generate", key, payload, {"batchId": str(batch.pk)})
    _audit("activation_generated", actor=actor, batchId=str(batch.pk), quantity=quantity)
    return batch, tuple(raw_codes)


def _get_batch(batch_id, *, lock=False):
    try:
        queryset = ActivationBatch.objects.select_for_update() if lock else ActivationBatch.objects
        return queryset.get(pk=batch_id)
    except (ActivationBatch.DoesNotExist, ValidationError, ValueError) as error:
        raise BusinessError("NOT_FOUND", "激活码批次不存在。", 404) from error


def batch_fingerprint(batch):
    return idempotency.payload_digest({
        "id": str(batch.pk), "state": batch.state, "version": batch.version,
        "receivedAt": batch.received_at,
        "codes": list(batch.codes.order_by("id").values("id", "state", "redeemed_at", "disabled_at")),
    })


@transaction.atomic
def transition_batch(batch_id, actor, action, expected_fingerprint=None, reason=""):
    if action not in TRANSITIONS:
        raise BusinessError("INVALID_ACTION", "批次操作无效。")
    before_states, next_state, permission = TRANSITIONS[action]
    _require_admin(actor, "activation." + permission)
    if action in ("disable", "void") and (not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500):
        raise BusinessError("REASON_REQUIRED", "请填写 1～500 字禁用或作废理由。")
    batch = _get_batch(batch_id, lock=True)
    if expected_fingerprint is not None and batch_fingerprint(batch) != expected_fingerprint:
        raise BusinessError("VERSION_CONFLICT", "批次状态或操作影响已变化，请重新确认。", 409)
    if batch.state == next_state:
        return batch
    if isinstance(before_states, str):
        before_states = (before_states,)
    if batch.state not in before_states or (action == "enable" and batch.received_at is None):
        raise BusinessError("BATCH_STATE_CONFLICT", "当前批次状态不允许此操作。请先确认文件领取，再启用。", 409)
    before = batch.state
    batch.state = next_state
    batch.version += 1
    if action == "receive":
        batch.received_by = actor
        batch.received_at = timezone.now()
    batch.save(_service=True)
    # No User lock here: batch administration must not reverse the student's
    # User -> reservation -> batch -> code ordering.
    _audit("activation_" + action, actor=actor, batchId=str(batch.pk), before=before, after=batch.state, reason=reason.strip())
    return batch


def code_fingerprint(code):
    return idempotency.payload_digest({"id": str(code.pk), "state": code.state, "redeemedAt": code.redeemed_at, "disabledAt": code.disabled_at, "batch": batch_fingerprint(code.batch)})


@transaction.atomic
def disable_code(code_id, actor, reason, expected_fingerprint=None):
    _require_admin(actor, "activation.disable_activationcode")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        raise BusinessError("REASON_REQUIRED", "请填写 1～500 字禁用理由。")
    try:
        reference = ActivationCode.objects.get(pk=code_id)
    except (ActivationCode.DoesNotExist, ValidationError, ValueError) as error:
        raise BusinessError("NOT_FOUND", "激活码记录不存在。", 404) from error
    _get_batch(reference.batch_id, lock=True)
    code = ActivationCode.objects.select_for_update().get(pk=code_id)
    if expected_fingerprint is not None and code_fingerprint(code) != expected_fingerprint:
        raise BusinessError("VERSION_CONFLICT", "激活码状态已变化，请重新确认。", 409)
    if code.state == "disabled":
        return code
    if code.state != "unused":
        raise BusinessError("CODE_ALREADY_USED", "已兑换码只能撤销对应权益，不能改回未使用。", 409)
    code.state = "disabled"
    code.disabled_at = timezone.now()
    code.reason = reason.strip()
    code.save(_service=True)
    _audit("activation_code_disabled", actor=actor, batchId=str(code.batch_id), codeId=str(code.pk), reason=code.reason)
    return code


@sensitive_variables("code", "digest", "payload")
@transaction.atomic
def redeem_code(user, code, key):
    digest = code_digest(code)
    user = lock_user(user)
    if not user.is_active or user.is_staff or user.is_superuser:
        raise BusinessError("FORBIDDEN", "当前账号不能使用学员兑换功能。", 403)
    lock_reservation(user)
    payload = {"codeDigest": digest}
    previous = idempotency.lookup(user, "activation.redeem", key, payload)
    if previous:
        return {**previous.response, "entitlement": entitlement_snapshot(user)}
    check_new_opening(user)
    reference = ActivationCode.objects.filter(digest=digest).first()
    if reference is None:
        raise BusinessError("CODE_UNAVAILABLE", "激活码不可用，请核对或联系发码管理员。", 409)
    batch = _get_batch(reference.batch_id, lock=True)
    current = ActivationCode.objects.select_for_update().get(pk=reference.pk)
    if batch.state != "enabled" or current.state != "unused":
        raise BusinessError("CODE_UNAVAILABLE", "激活码不可用，请核对或联系发码管理员。", 409)
    source = grant_entitlement(user, "activation", str(current.pk))
    if source.status != "active":
        raise BusinessError("CODE_UNAVAILABLE", "激活码不可用，请核对或联系发码管理员。", 409)
    current.state = "redeemed"
    current.redeemed_by = user
    current.redeemed_at = timezone.now()
    current.save(_service=True)
    redemption = ActivationRedemption(code=current, user=user, source=source)
    redemption.save(_service=True)
    history = {"redemptionId": str(redemption.pk), "redeemedAt": redemption.redeemed_at.isoformat()}
    idempotency.remember(user, "activation.redeem", key, payload, history)
    _audit("activation_redeemed", user=user, source=source, batchId=str(batch.pk), codeId=str(current.pk), redemptionId=str(redemption.pk))
    return {**history, "entitlement": entitlement_snapshot(user)}
