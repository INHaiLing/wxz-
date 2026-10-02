from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from common.errors import BusinessError
from entitlements.models import AuditEvent, EntitlementSource
from payments.models import PaymentEvent, PaymentTask
from payments.synchronization import apply_query
from payments.tasks import MAX_ATTEMPTS, claim_task, finish_task, run_task
from .sync_fixtures import SyncFixture, snapshot
from .test_preparation import PAYMENT_SETTINGS


@override_settings(**PAYMENT_SETTINGS)
class PaymentTaskTests(SyncFixture):
    def test_query_then_delivery_via_management_command(self):
        with patch("payments.gateway.query_order", return_value=snapshot(self.order)), patch("payments.gateway.notify_goods") as delivered:
            call_command("run_payment_tasks", limit=2, stdout=StringIO())
            self.assertEqual(delivered.call_count, 1)
        self.assertEqual(PaymentTask.objects.filter(status="done").count(), 2)
        self.assertEqual(EntitlementSource.objects.count(), 1)

    def test_errors_pending_platform_and_exhausted_retries_do_not_fake_success(self):
        claimed = claim_task()
        with patch("payments.gateway.query_order", side_effect=BusinessError("PLATFORM_UNAVAILABLE", "失败", 503)):
            run_task(*claimed)
        task = PaymentTask.objects.get()
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.last_error_code, "PLATFORM_UNAVAILABLE")
        task.attempts = MAX_ATTEMPTS - 1
        task.next_run_at = timezone.now() - timedelta(seconds=1)
        task.save(_service=True)
        claimed = claim_task()
        with patch("payments.gateway.query_order", return_value=snapshot(self.order, 1)):
            run_task(*claimed)
        task.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.last_error_code, "PLATFORM_PENDING")
        self.assertEqual(EntitlementSource.objects.count(), 0)
        self.assertIsNone(claim_task())

    def test_expired_lease_takeover_and_stale_worker_cannot_finish(self):
        first_id, first_lease = claim_task()
        task = PaymentTask.objects.get(pk=first_id)
        task.lease_until = timezone.now() - timedelta(seconds=1)
        task.save(_service=True)
        self.assertFalse(finish_task(first_id, first_lease))
        second_id, second_lease = claim_task()
        self.assertEqual(first_id, second_id)
        self.assertNotEqual(first_lease, second_lease)
        self.assertFalse(finish_task(first_id, first_lease))
        self.assertTrue(finish_task(second_id, second_lease))
        task.refresh_from_db()
        self.assertEqual(task.attempts, 2)
        self.assertEqual(task.status, "done")

    def test_delivery_error_retries_and_refund_prevents_late_delivery(self):
        apply_query(self.order.pk, snapshot(self.order))
        query = PaymentTask.objects.get(kind="query")
        query.status = "done"
        query.save(_service=True)
        claimed = claim_task()
        with patch("payments.gateway.notify_goods", side_effect=BusinessError("PLATFORM_UNAVAILABLE", "错误", 503)):
            run_task(*claimed)
        self.assertEqual(PaymentTask.objects.get(kind="deliver").status, "pending")
        apply_query(self.order.pk, snapshot(self.order, 5))
        task = PaymentTask.objects.get(kind="deliver")
        task.next_run_at = timezone.now()
        task.save(_service=True)
        with patch("payments.gateway.notify_goods") as notify:
            run_task(*claim_task())
            notify.assert_not_called()
        self.assertEqual(PaymentTask.objects.get(kind="deliver").status, "done")

    def test_daily_reconciliation_reschedules_completed_queries_but_not_failed(self):
        task = PaymentTask.objects.get()
        task.status = "done"
        with patch("django.utils.timezone.now", return_value=timezone.now() - timedelta(days=2)):
            task.save(_service=True)
        call_command("schedule_payment_reconciliation", stdout=StringIO())
        task.refresh_from_db()
        self.assertEqual(task.status, "pending")
        task.status = "failed"
        task.save(_service=True)
        call_command("schedule_payment_reconciliation", stdout=StringIO())
        task.refresh_from_db()
        self.assertEqual(task.status, "failed")


@override_settings(**PAYMENT_SETTINGS)
class PaymentTaskAdminTests(SyncFixture):
    def setUp(self):
        super().setUp()
        self.task = PaymentTask.objects.get()
        self.task.status = "failed"
        self.task.last_error_code = "PLATFORM_UNAVAILABLE"
        self.task.save(_service=True)
        self.url = reverse("admin:payments_task_reschedule", args=(self.task.pk,))
        self.client.force_login(self.actor)

    def post(self, token, reason="核实平台状态后重试"):
        return self.client.post(self.url, {"confirmation_token": token, "confirm": "yes", "reason": reason})

    def test_actual_preview_reason_confirmation_and_readonly_objects(self):
        preview = self.client.get(self.url)
        self.assertContains(preview, "不会直接付款")
        token = preview.context["confirmation_token"]
        self.assertEqual(self.post(token, " ").status_code, 400)
        self.assertEqual(self.post(token).status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "pending")
        self.assertEqual(self.post(token).status_code, 409)
        self.assertEqual(AuditEvent.objects.filter(kind="payment_task_rescheduled").count(), 1)
        for name, pk in (("order", self.order.pk), ("paymenttask", self.task.pk)):
            self.assertEqual(self.client.post(reverse(f"admin:payments_{name}_change", args=(pk,)), {"status": "fulfilled"}).status_code, 403)
            self.assertEqual(self.client.get(reverse(f"admin:payments_{name}_delete", args=(pk,))).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:payments_paymentevent_add")).status_code, 403)

    def test_signature_permission_csrf_and_changed_task_cannot_bypass_preview(self):
        token = self.client.get(self.url).context["confirmation_token"]
        self.assertEqual(self.post(token + "tampered").status_code, 409)
        other = get_user_model().objects.create_user(username="task-viewer", is_staff=True)
        other.user_permissions.add(Permission.objects.get(content_type__app_label="payments", codename="view_paymenttask"))
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.post(token).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.actor)
        self.assertEqual(csrf.post(self.url, {"confirmation_token": token, "confirm": "yes", "reason": "核查"}).status_code, 403)
        self.client.force_login(self.actor)
        self.task.last_error_code = "CHANGED"
        self.task.save(_service=True)
        response = self.post(token)
        self.assertEqual(response.status_code, 409)
        self.assertNotContains(response, 'name="confirmation_token"', status_code=409)
