"""Durable bounded workers. Never hold a task row lock while acquiring User."""
import secrets
from datetime import timedelta

from django.db import connection, models, transaction
from django.utils import timezone

from common.errors import BusinessError
from common.idempotency import payload_digest
from entitlements.services import _audit, _require_admin, lock_reservation, lock_user
from . import gateway
from .models import Order, PaymentTask
from .synchronization import apply_query

MAX_ATTEMPTS = 8
LEASE_SECONDS = 60


@transaction.atomic
def claim_task():
    now = timezone.now()
    eligible = models.Q(status="pending", next_run_at__lte=now) | models.Q(status="running", lease_until__lte=now)
    queryset = PaymentTask.objects.filter(eligible).order_by("next_run_at", "pk")
    queryset = queryset.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else queryset.select_for_update()
    task = queryset.first()
    if task is None:
        return None
    if task.attempts >= MAX_ATTEMPTS:
        task.status = "failed"
        task.lease_token = ""
        task.lease_until = None
        task.last_error_code = "RETRIES_EXHAUSTED"
        task.save(_service=True)
        return None
    task.status = "running"
    task.attempts += 1
    task.lease_token = secrets.token_hex(32)
    task.lease_until = now + timedelta(seconds=LEASE_SECONDS)
    task.save(_service=True)
    return task.pk, task.lease_token


@transaction.atomic
def finish_task(task_id, lease, *, error=""):
    task = PaymentTask.objects.select_for_update().get(pk=task_id)
    if task.status != "running" or not task.lease_token or task.lease_token != lease:
        return False
    task.last_error_code = error[:64]
    if error:
        task.status = "failed" if task.attempts >= MAX_ATTEMPTS else "pending"
        task.next_run_at = timezone.now() + timedelta(seconds=min(30 * 2**(task.attempts - 1), 3600))
    else:
        task.status = "done"
    task.lease_token = ""
    task.lease_until = None
    task.save(_service=True)
    return True


def run_task(task_id, lease):
    task = PaymentTask.objects.get(pk=task_id)
    if task.status != "running" or task.lease_token != lease:
        return False
    order = Order.objects.select_related("identity").get(pk=task.order_id)
    error = ""
    try:
        if task.kind == "query":
            if order.status not in ("closed", "refunded"):
                result = apply_query(order.pk, gateway.query_order(order))
                if result["order"]["status"] in ("preparing", "paid"):
                    error = "PLATFORM_PENDING"
                elif result["order"]["status"] == "review":
                    error = "PAYMENT_REVIEW_REQUIRED"
        elif task.kind == "deliver":
            if order.status == "fulfilled":
                gateway.notify_goods(order)
            elif order.status not in ("closed", "refunded"):
                error = "PAYMENT_REVIEW_REQUIRED"
        else:
            error = "INVALID_TASK_KIND"
    except BusinessError as exception:
        error = str(exception.detail["error"]["code"])
    except Exception:
        # Only safe diagnostic codes persist. Unexpected failures remain failed
        # work, never masquerading as payment or delivery success.
        error = "UNEXPECTED_TASK_ERROR"
    return finish_task(task_id, lease, error=error)


def task_fingerprint(task):
    return payload_digest({"id": task.pk, "state": task.status, "attempts": task.attempts,
                           "lease": task.lease_token, "until": task.lease_until, "updated": task.updated_at})


@transaction.atomic
def reschedule_task(task_id, actor, reason, fingerprint):
    _require_admin(actor, "payments.reschedule_paymenttask")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        raise BusinessError("REASON_REQUIRED", "请填写 1～500 字核查及重调度理由。")
    reference = PaymentTask.objects.select_related("order").get(pk=task_id)
    user = lock_user(reference.order.user_id)
    lock_reservation(user)
    Order.objects.select_for_update().get(pk=reference.order_id)
    task = PaymentTask.objects.select_for_update().get(pk=task_id)
    if task_fingerprint(task) != fingerprint or task.status != "failed":
        raise BusinessError("VERSION_CONFLICT", "任务已变化或并非失败状态，请重新预览。", 409)
    task.status = "pending"
    task.attempts = 0
    task.next_run_at = timezone.now()
    task.lease_token = ""
    task.lease_until = None
    task.last_error_code = ""
    task.save(_service=True)
    _audit("payment_task_rescheduled", user=user, actor=actor, order_id=task.order_id, taskId=task.pk, reason=reason.strip())
    return task


@transaction.atomic
def schedule_reconciliation(order_id, *, cutoff):
    reference = Order.objects.get(pk=order_id)
    user = lock_user(reference.user_id)
    lock_reservation(user)
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status not in ("preparing", "paid", "fulfilled", "review"):
        return False
    task = PaymentTask.objects.select_for_update().get(order=order, kind="query")
    # Failed tasks require explicit human preview/reason. Do not silently reset
    # their retry budget from the scheduler.
    if task.status != "done" or task.updated_at > cutoff:
        return False
    task.status = "pending"
    task.attempts = 0
    task.next_run_at = timezone.now()
    task.save(_service=True)
    return True
