import csv
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from activation.models import ActivationBatch, ActivationCode, ActivationRedemption
from activation.services import generate_batch, redeem_code, transition_batch
from common.models import IdempotencyRecord
from entitlements.models import AuditEvent
from entitlements.services import has_active_entitlement


class ActivationAdminTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(username="codes-manager", is_staff=True)
        self.other_admin = users.objects.create_user(username="codes-second-manager", is_staff=True)
        self.viewer = users.objects.create_user(username="codes-viewer", is_staff=True)
        self.user = users.objects.create_user(username="codes-student")
        permissions = Permission.objects.filter(content_type__app_label__in=("activation", "entitlements"))
        self.admin.user_permissions.set(permissions)
        self.other_admin.user_permissions.set(permissions)
        self.viewer.user_permissions.set(permissions.filter(codename__startswith="view_"))
        self.client.force_login(self.admin)
        self.generate_url = reverse("admin:activation_batch_generate")

    def generate(self, key="admin-generate-1", quantity=2):
        response = self.client.post(self.generate_url, {"quantity": quantity, "label": "测试领取", "idempotency_key": key})
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        rows = list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["序号", "永久激活码"])
        return ActivationBatch.objects.get(pk=response["X-Activation-Batch-ID"]), [row[1] for row in rows[1:]]

    def transition_url(self, batch, action):
        return reverse("admin:activation_batch_transition", args=(batch.pk, action))

    def confirm(self, url, token=None, reason="人工核对后操作"):
        if token is None:
            preview = self.client.get(url)
            self.assertEqual(preview.status_code, 200)
            token = preview.context["confirmation_token"]
        return self.client.post(url, {"confirmation_token": token, "confirm": "yes", "reason": reason})

    def test_csv_once_receipt_then_enable_and_redeem_complete_http_flow(self):
        changelist = self.client.get(reverse("admin:activation_activationbatch_changelist"))
        self.assertContains(changelist, "生成并一次下载 CSV")
        self.assertContains(self.client.get(self.generate_url), "idempotency_key")
        batch, raw = self.generate()
        stored = str(list(ActivationCode.objects.values())) + str(list(IdempotencyRecord.objects.values())) + str(list(AuditEvent.objects.values()))
        for code in raw:
            self.assertNotIn(code, stored)
        self.assertEqual(self.confirm(self.transition_url(batch, "enable")).status_code, 409)
        repeat = self.client.post(self.generate_url, {"quantity": 2, "label": "测试领取", "idempotency_key": "admin-generate-1"})
        self.assertEqual(repeat.status_code, 302)
        self.assertNotIn("Content-Disposition", repeat)
        self.assertEqual(ActivationBatch.objects.count(), 1)
        detail = self.client.get(repeat["Location"])
        for code in raw:
            self.assertNotContains(detail, code)
        self.assertEqual(self.confirm(self.transition_url(batch, "receive")).status_code, 302)
        self.assertEqual(self.confirm(self.transition_url(batch, "enable")).status_code, 302)
        result = redeem_code(self.user, raw[0], "admin-redeem-1")
        self.assertTrue(result["entitlement"]["permanent"])
        batch.refresh_from_db()
        self.assertEqual(batch.received_by, self.admin)
        self.assertIsNotNone(batch.received_at)
        unavailable = self.client.get(f"/admin/activation/activationbatch/{batch.pk}/download/", follow=True)
        self.assertNotIn("Content-Disposition", unavailable)
        for code in raw:
            self.assertNotIn(code.encode(), unavailable.content)

    def test_download_loss_void_old_batch_then_generate_fresh_batch(self):
        batch, raw = self.generate()
        self.assertEqual(self.confirm(self.transition_url(batch, "void"), reason="CSV 下载失败").status_code, 302)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "void")
        repeated = self.client.post(self.generate_url, {"quantity": 2, "label": "测试领取", "idempotency_key": "admin-generate-1"})
        self.assertEqual(repeated.status_code, 302)
        replacement, new_raw = self.generate("admin-generate-2")
        self.assertNotEqual(batch.pk, replacement.pk)
        self.assertTrue(set(raw).isdisjoint(new_raw))
        self.assertEqual(self.confirm(self.transition_url(batch, "receive")).status_code, 409)
        self.assertEqual(self.confirm(self.transition_url(batch, "enable")).status_code, 409)
        self.assertEqual(AuditEvent.objects.filter(kind="activation_void").get().details["reason"], "CSV 下载失败")

    def test_changed_generate_parameters_and_missing_key_are_rejected(self):
        self.generate()
        conflict = self.client.post(self.generate_url, {"quantity": 3, "label": "测试领取", "idempotency_key": "admin-generate-1"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.client.post(self.generate_url, {"quantity": 1}).status_code, 400)
        self.assertEqual(ActivationBatch.objects.count(), 1)
        self.assertEqual(ActivationCode.objects.count(), 2)

    def test_batch_disable_preserves_redeemed_rights_and_single_code_disable_is_final(self):
        batch, raw = self.generate(quantity=3)
        self.confirm(self.transition_url(batch, "receive"))
        self.confirm(self.transition_url(batch, "enable"))
        redeem_code(self.user, raw[0], "admin-redeem-1")
        self.assertEqual(self.confirm(self.transition_url(batch, "disable"), reason="暂停发码").status_code, 302)
        self.assertTrue(has_active_entitlement(self.user))
        self.assertEqual(ActivationCode.objects.filter(state="redeemed").count(), 1)
        self.assertEqual(self.confirm(self.transition_url(batch, "enable")).status_code, 302)
        code = ActivationCode.objects.filter(batch=batch, state="unused").first()
        url = reverse("admin:activation_code_disable", args=(code.pk,))
        self.assertEqual(self.confirm(url, reason="误发未使用码").status_code, 302)
        code.refresh_from_db()
        self.assertEqual(code.state, "disabled")
        used = ActivationCode.objects.get(state="redeemed")
        self.assertEqual(self.confirm(reverse("admin:activation_code_disable", args=(used.pk,))).status_code, 409)
        self.assertTrue(has_active_entitlement(self.user))

    def test_confirmation_binds_actor_action_batch_and_original_impact(self):
        batch, _ = self.generate()
        url = self.transition_url(batch, "receive")
        token = self.client.get(url).context["confirmation_token"]
        for bad in ("", token + "tampered"):
            self.assertEqual(self.confirm(url, bad).status_code, 409)
        self.client.force_login(self.other_admin)
        self.assertEqual(self.confirm(url, token).status_code, 409)
        self.client.force_login(self.admin)
        other, _ = self.generate("admin-generate-2")
        self.assertEqual(self.confirm(self.transition_url(other, "receive"), token).status_code, 409)
        self.assertEqual(self.confirm(self.transition_url(batch, "void"), token).status_code, 409)
        with patch("django.core.signing.time.time", return_value=100):
            expired = self.client.get(url).context["confirmation_token"]
        with patch("django.core.signing.time.time", return_value=1901):
            self.assertEqual(self.confirm(url, expired).status_code, 409)
        code = batch.codes.first()
        disable_url = reverse("admin:activation_code_disable", args=(code.pk,))
        self.assertEqual(self.confirm(disable_url).status_code, 302)
        changed = self.confirm(url, token)
        self.assertEqual(changed.status_code, 409)
        self.assertNotContains(changed, 'name="confirmation_token"', status_code=409)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "awaiting_receipt")

    def test_admin_permissions_csrf_and_service_only_records(self):
        batch, _ = self.generate()
        urls = [self.generate_url] + [self.transition_url(batch, action) for action in ("receive", "enable", "disable", "void")]
        code = batch.codes.first()
        urls.append(reverse("admin:activation_code_disable", args=(code.pk,)))
        self.client.force_login(self.viewer)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {"confirm": "yes"}).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        for url in urls:
            self.assertEqual(csrf_client.post(url, {"confirm": "yes"}).status_code, 403)
        self.client.force_login(self.admin)
        for model, obj in (("activationbatch", batch), ("activationcode", code)):
            self.assertEqual(self.client.get(reverse(f"admin:activation_{model}_add")).status_code, 403)
            self.assertEqual(self.client.get(reverse(f"admin:activation_{model}_delete", args=(obj.pk,))).status_code, 403)
            self.assertEqual(self.client.post(reverse(f"admin:activation_{model}_change", args=(obj.pk,)), {"state": "enabled", "_save": "保存"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:activation_activationredemption_add")).status_code, 403)

    def test_empty_reason_rejected_and_misissue_revoke_keeps_code_used(self):
        batch, raw = self.generate()
        self.assertEqual(self.confirm(self.transition_url(batch, "void"), reason=" ").status_code, 400)
        self.confirm(self.transition_url(batch, "receive"))
        self.confirm(self.transition_url(batch, "enable"))
        result = redeem_code(self.user, raw[0], "admin-redeem-1")
        redemption = ActivationRedemption.objects.get()
        detail = self.client.get(reverse("admin:activation_activationcode_change", args=(redemption.code_id,)))
        self.assertContains(detail, "查看对应权益及误发撤权")
        revoke_url = reverse("admin:entitlements_source_revoke", args=(redemption.source_id,))
        self.assertEqual(self.confirm(revoke_url, reason="误发码已兑换").status_code, 302)
        replay = redeem_code(self.user, raw[0], "admin-redeem-1")
        self.assertEqual(replay["redemptionId"], result["redemptionId"])
        self.assertFalse(replay["entitlement"]["active"])
        redemption.code.refresh_from_db()
        self.assertEqual(redemption.code.state, "redeemed")
