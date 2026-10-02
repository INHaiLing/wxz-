import base64
from datetime import timedelta

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import StudentSession
from accounts.services import issue_student_session
from accounts.wechat import WeChatLogin
from entitlements.models import Product
from entitlements.services import grant_entitlement, mark_product_synced


@override_settings(
    STUDENT_SESSION_ENCRYPTION_KEYS=(Fernet.generate_key().decode(),),
    VIRTUAL_PAYMENT_ENABLED=False, VIRTUAL_PAYMENT_ANDROID_ENABLED=True,
    VIRTUAL_PAYMENT_IOS_ENABLED=False,
)
class EntitlementAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.product = Product.objects.create(platform_product_id="api-bank")
        self.admin = get_user_model().objects.create_user(username="product-api-manager", is_staff=True)
        self.admin.user_permissions.add(Permission.objects.get(content_type__app_label="entitlements", codename="sync_product"))
        mark_product_synced(self.product.pk, self.admin, price_fen=1000, platform_product_id="api-bank")
        self.product.refresh_from_db()

    def session(self, openid="rights-owner"):
        return issue_student_session(WeChatLogin("wx-rights-test", openid, base64.b64encode(b"test-wechat-key").decode()))

    def test_product_anonymous_contract_and_disabled_channel(self):
        response = self.client.get("/api/student/v1/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        product = response.data["results"][0]
        self.assertEqual(product["priceFen"], 1000)
        self.assertTrue(product["permanent"])
        self.assertIsNone(product["expiresAt"])
        self.assertFalse(product["purchaseAvailable"])
        self.assertEqual(product["unavailableReason"], "PAYMENT_CHANNEL_UNAVAILABLE")
        self.assertEqual(product["paymentChannels"], {"android": False, "ios": False})
        self.assertNotIn("platformProductId", product)
        self.assertNotIn("secret", str(response.data).lower())

    def test_channel_flags_and_price_sync_gate(self):
        with override_settings(VIRTUAL_PAYMENT_ENABLED=True, VIRTUAL_PAYMENT_IOS_ENABLED=True):
            product = self.client.get("/api/student/v1/products/").data["results"][0]
            self.assertTrue(product["purchaseAvailable"])
            self.assertEqual(product["paymentChannels"], {"android": True, "ios": True})
            self.product.price_fen = 2000
            self.product.save()
            product = self.client.get("/api/student/v1/products/").data["results"][0]
            self.assertFalse(product["purchaseAvailable"])
            self.assertEqual(product["unavailableReason"], "PLATFORM_SYNC_REQUIRED")
            self.product.is_active = False
            self.product.save()
            product = self.client.get("/api/student/v1/products/").data["results"][0]
            self.assertEqual(product["unavailableReason"], "PRODUCT_DISABLED")

    def test_current_entitlement_is_owner_only_and_cookie_is_not_student_login(self):
        self.assertEqual(self.client.get("/api/student/v1/me/entitlements/").status_code, 401)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/api/student/v1/me/entitlements/").status_code, 401)
        token, session = self.session()
        _, other = self.session("rights-other")
        grant_entitlement(other.user, "activation", "other-code")
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)
        response = self.client.get(f"/api/student/v1/me/entitlements/?userId={other.user_id}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["active"])
        grant_entitlement(session.user, "activation", "owner-code")
        response = self.client.get("/api/student/v1/me/entitlements/")
        self.assertEqual(response.data, {"scope": "all_chinese", "active": True, "permanent": True, "expiresAt": None})
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn("owner-code", str(response.data))

    def test_invalid_expired_revoked_and_disabled_bearers_do_not_become_anonymous(self):
        for header in ("Bearer invalid", "Basic abc", "Bearer " + "a" * 43):
            self.client.credentials(HTTP_AUTHORIZATION=header)
            response = self.client.get("/api/student/v1/products/")
            self.assertEqual(response.status_code, 401)
            self.assertIn("requestId", response.data)
        for field, value in (("expires_at", timezone.now() - timedelta(seconds=1)), ("revoked_at", timezone.now())):
            token, session = self.session(field)
            StudentSession.objects.filter(pk=session.pk).update(**{field: value})
            self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)
            self.assertEqual(self.client.get("/api/student/v1/me/entitlements/").status_code, 401)
        token, session = self.session("disabled")
        session.user.is_active = False
        session.user.save()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)
        self.assertEqual(self.client.get("/api/student/v1/products/").status_code, 401)
