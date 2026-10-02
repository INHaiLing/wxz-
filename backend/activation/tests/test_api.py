import base64
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.services import issue_student_session
from accounts.wechat import WeChatLogin
from activation.models import ActivationCode, ActivationRedemption
from activation.services import generate_batch, transition_batch
from common.models import RateBucket
from entitlements.services import reserve_opening, revoke_entitlement


@override_settings(STUDENT_SESSION_ENCRYPTION_KEYS=(Fernet.generate_key().decode(),), ACTIVATION_REDEEM_RATE_LIMIT=1000)
class ActivationAPITests(TestCase):
    url = "/api/student/v1/activation/redeem/"

    def setUp(self):
        self.client = APIClient()
        self.admin = get_user_model().objects.create_user(username="redeem-api-manager", is_staff=True)
        self.admin.user_permissions.set(Permission.objects.filter(content_type__app_label__in=("activation", "entitlements")))
        batch, self.raw = generate_batch(self.admin, 2, "API 测试", "generate-api-1")
        transition_batch(batch.pk, self.admin, "receive")
        transition_batch(batch.pk, self.admin, "enable")
        token, self.session = issue_student_session(WeChatLogin("wx-activation-test", "api-owner", base64.b64encode(b"test-session-key").decode()))
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def post(self, code=None, key="api-redeem-1", **payload):
        return self.client.post(self.url, {"code": code or self.raw[0], **payload}, format="json", HTTP_IDEMPOTENCY_KEY=key)

    def test_actual_bearer_contract_replay_and_current_rights(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"redemptionId", "redeemedAt", "entitlement"})
        self.assertEqual(response.data["entitlement"], {"scope": "all_chinese", "active": True, "permanent": True, "expiresAt": None})
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn(self.raw[0], str(response.data))
        replay = self.post(code=self.raw[0].lower().replace("-", " "))
        self.assertEqual(replay.data, response.data)
        blocked = self.post(self.raw[1], "api-redeem-2")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.data["error"]["code"], "ALREADY_ACTIVATED")
        self.assertIn("requestId", blocked.data)
        self.assertEqual(ActivationCode.objects.filter(state="unused").count(), 1)
        redemption = ActivationRedemption.objects.get()
        revoke_entitlement(redemption.source_id, self.admin, "误发撤销")
        after_revoke = self.post()
        self.assertEqual(after_revoke.data["redemptionId"], response.data["redemptionId"])
        self.assertFalse(after_revoke.data["entitlement"]["active"])

    def test_auth_cookie_and_disabled_user_cannot_redeem(self):
        self.client.credentials()
        self.assertEqual(self.post().status_code, 401)
        self.client.force_login(self.admin)
        self.assertEqual(self.post().status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
        self.assertEqual(self.post().status_code, 401)
        token, session = issue_student_session(WeChatLogin("wx-activation-test", "api-disabled", base64.b64encode(b"test-session-key").decode()))
        session.user.is_active = False
        session.user.save()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(ActivationRedemption.objects.count(), 0)

    def test_validation_idempotency_conflict_and_reservation_do_not_consume(self):
        for payload in ({}, {"code": "x" * 129}, {"code": self.raw[0], "userId": "someone"}):
            self.assertEqual(self.client.post(self.url, payload, format="json", HTTP_IDEMPOTENCY_KEY="api-redeem-1").status_code, 400)
        self.assertEqual(self.post(key="").status_code, 400)
        self.assertEqual(self.post(code="invalid").status_code, 400)
        reserve_opening(self.session.user, "pending-order")
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "PURCHASE_IN_PROGRESS")
        self.assertEqual(ActivationCode.objects.filter(state="unused").count(), 2)
        self.assertEqual(ActivationRedemption.objects.count(), 0)

    def test_failed_redemption_and_invalid_body_are_rate_limited_before_transaction(self):
        with override_settings(ACTIVATION_REDEEM_RATE_LIMIT=1), patch("common.limits.time.time", return_value=100):
            failed = self.post(code="QM-" + "A" * 52)
            self.assertEqual(failed.status_code, 409)
            rejected = self.post()
            self.assertEqual(rejected.status_code, 429)
            self.assertEqual(rejected.data["error"]["code"], "RATE_LIMITED")
            self.assertEqual(RateBucket.objects.get(scope="activation-user").count, 1)
        self.assertEqual(ActivationRedemption.objects.count(), 0)
        self.assertEqual(ActivationCode.objects.filter(state="unused").count(), 2)
        with override_settings(ACTIVATION_REDEEM_RATE_LIMIT=1), patch("common.limits.time.time", return_value=180):
            invalid = self.client.post(self.url, {"extra": "bad"}, format="json")
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(self.post().status_code, 429)

    def test_ip_limit_is_shared_and_untrusted_forwarding_cannot_bypass_it(self):
        with patch("common.limits.time.time", return_value=100):
            # Fill the real HMAC bucket through the existing helper; no IP hashes
            # or student identifiers need to be reproduced in implementation.
            from common.limits import check_rate
            check_rate("activation-ip", "127.0.0.1", 3000)
            real = RateBucket.objects.get(scope="activation-ip")
            RateBucket.objects.filter(pk=real.pk).update(count=3000)
            response = self.client.post(self.url, {"code": self.raw[0]}, format="json", HTTP_IDEMPOTENCY_KEY="api-redeem-1", HTTP_X_REAL_IP="8.8.8.8", HTTP_X_FORWARDED_FOR="8.8.8.8")
            self.assertEqual(response.status_code, 429)
        self.assertEqual(ActivationRedemption.objects.count(), 0)
