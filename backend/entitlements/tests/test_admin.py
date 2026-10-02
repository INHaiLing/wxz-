from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from entitlements.models import AuditEvent, EntitlementSource, Product
from entitlements.services import grant_entitlement, mark_product_synced


class EntitlementAdminTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.user = users.objects.create_user(username="admin-rights-student")
        self.admin = users.objects.create_user(username="admin-rights-manager", is_staff=True)
        self.other_admin = users.objects.create_user(username="admin-rights-other", is_staff=True)
        self.viewer = users.objects.create_user(username="admin-rights-viewer", is_staff=True)
        permissions = Permission.objects.filter(content_type__app_label="entitlements")
        self.admin.user_permissions.set(permissions)
        self.other_admin.user_permissions.set(permissions)
        self.viewer.user_permissions.set(permissions.filter(codename__startswith="view_"))
        self.source = grant_entitlement(self.user, "activation", "admin-code-1")
        self.client.force_login(self.admin)
        self.revoke_url = reverse("admin:entitlements_source_revoke", args=(self.source.pk,))

    def token(self, url=None):
        response = self.client.get(url or self.revoke_url)
        self.assertEqual(response.status_code, 200)
        return response.context["confirmation_token"]

    def revoke(self, token, **extra):
        return self.client.post(self.revoke_url, {"confirmation_token": token, "reason": "误发激活码", "confirm": "yes", **extra})

    def test_revoke_preview_impact_confirm_and_other_source_remains(self):
        grant_entitlement(self.user, "payment", "admin-order-1")
        preview = self.client.get(self.revoke_url)
        self.assertContains(preview, "仍有永久权益")
        self.assertEqual(self.revoke(preview.context["confirmation_token"]).status_code, 302)
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, "revoked")
        self.assertEqual(self.source.reason, "误发激活码")
        self.assertEqual(EntitlementSource.objects.filter(status="active").count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="revoked").count(), 1)

    def test_revoke_missing_tampered_cross_user_expired_and_changed_impact_are_409(self):
        token = self.token()
        for bad_token in ("", token + "tampered"):
            self.assertEqual(self.revoke(bad_token).status_code, 409)
        self.client.force_login(self.other_admin)
        self.assertEqual(self.revoke(token).status_code, 409)
        self.client.force_login(self.admin)
        with patch("django.core.signing.time.time", return_value=100):
            expired = self.token()
        with patch("django.core.signing.time.time", return_value=1901):
            self.assertEqual(self.revoke(expired).status_code, 409)
        grant_entitlement(self.user, "payment", "changed-impact")
        self.assertEqual(self.revoke(token).status_code, 409)
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, "active")
        self.assertEqual(AuditEvent.objects.filter(kind="revoked").count(), 0)

    def test_revoke_requires_reason_permission_and_csrf(self):
        token = self.token()
        self.assertEqual(self.revoke(token, reason=" ").status_code, 400)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.revoke_url).status_code, 403)
        self.assertEqual(self.revoke(token).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(self.revoke_url, {"confirmation_token": token, "reason": "误发", "confirm": "yes"}).status_code, 403)
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, "active")

    def test_source_audit_and_reservation_cannot_be_mutated_by_admin_routes(self):
        for model, object_id in (("entitlementsource", self.source.pk), ("auditevent", AuditEvent.objects.first().pk)):
            with self.subTest(model=model):
                change = reverse(f"admin:entitlements_{model}_change", args=(object_id,))
                delete = reverse(f"admin:entitlements_{model}_delete", args=(object_id,))
                add = reverse(f"admin:entitlements_{model}_add")
                self.assertEqual(self.client.get(change).status_code, 200)
                self.assertEqual(self.client.post(change, {"status": "active", "_save": "保存"}).status_code, 403)
                self.assertEqual(self.client.get(delete).status_code, 403)
                self.assertEqual(self.client.get(add).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:entitlements_openingreservation_add")).status_code, 403)

    def test_product_edit_clears_sync_and_sync_confirmation_binds_current_price(self):
        product = Product.objects.create(platform_product_id="bank-001")
        mark_product_synced(product.pk, self.admin, price_fen=1000, platform_product_id="bank-001")
        url = reverse("admin:entitlements_product_change", args=(product.pk,))
        response = self.client.post(url, {"name": product.name, "price_fen": 2000, "is_active": "on", "platform_product_id": "bank-001", "_save": "保存", "platform_sync_state": "synced"})
        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.platform_sync_state, "pending")
        self.assertEqual(AuditEvent.objects.filter(kind="product_changed").count(), 1)
        sync_url = reverse("admin:entitlements_product_sync", args=(product.pk,))
        token = self.token(sync_url)
        product.price_fen = 3000
        product.save()
        self.assertEqual(self.client.post(sync_url, {"confirmation_token": token, "confirm": "yes"}).status_code, 409)
        fresh = self.token(sync_url)
        self.assertEqual(self.client.post(sync_url, {"confirmation_token": fresh, "confirm": "yes"}).status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.platform_synced_price_fen, 3000)
        self.assertEqual(product.platform_sync_state, "synced")

    def test_sync_confirmation_requires_permission_and_csrf(self):
        product = Product.objects.create(platform_product_id="bank-001")
        sync_url = reverse("admin:entitlements_product_sync", args=(product.pk,))
        token = self.token(sync_url)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(sync_url).status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(sync_url, {"confirmation_token": token, "confirm": "yes"}).status_code, 403)
        product.refresh_from_db()
        self.assertEqual(product.platform_sync_state, "pending")

    def test_payment_source_has_no_manual_refund_or_revoke_route(self):
        payment = grant_entitlement(self.user, "payment", "paid-readonly")
        url = reverse("admin:entitlements_source_revoke", args=(payment.pk,))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"confirm": "yes", "reason": "误操作"}).status_code, 403)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "active")
