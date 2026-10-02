import base64
import hashlib
import json
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from common.errors import BusinessError
from common.models import RateBucket
from accounts.models import StudentSession, User, WeChatIdentity
from accounts.services import decrypt_wechat_session_key, issue_student_session
from accounts.wechat import NoRedirect, WeChatLogin, exchange_code


TEST_KEY = Fernet.generate_key().decode()
WECHAT_KEY = base64.b64encode(b"test-wechat-key!!").decode()
LOGIN = WeChatLogin("wx-test-app", "test-openid", WECHAT_KEY)


@override_settings(
    ROOT_URLCONF="accounts.tests.student_urls", STUDENT_SESSION_ENCRYPTION_KEYS=(TEST_KEY,),
    WECHAT_APP_ID="wx-test-app", WECHAT_APP_SECRET="test-secret-not-a-real-credential",
    WECHAT_LOGIN_RATE_LIMIT=1000,
)
class StudentIdentityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/student/v1/auth/wechat/"

    def login(self, login=LOGIN, **kwargs):
        with patch("accounts.student_api.exchange_code", return_value=login):
            return self.client.post(self.url, {"code": "test-login-code"}, format="json", **kwargs)

    def test_login_is_unique_and_opaque_credentials_are_only_returned_once(self):
        start = timezone.now()
        first, second = self.login(), self.login()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["user"], second.data["user"])
        self.assertNotEqual(first.data["token"], second.data["token"])
        self.assertEqual(len(first.data["token"]), 43)
        self.assertIn("no-store", first["Cache-Control"])
        self.assertEqual(WeChatIdentity.objects.count(), 1)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(StudentSession.objects.count(), 2)
        user = User.objects.get()
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        session = StudentSession.objects.first()
        self.assertGreaterEqual(session.expires_at, start + timedelta(days=7))
        self.assertLess(session.expires_at, start + timedelta(days=7, seconds=5))
        stored = json.dumps(list(StudentSession.objects.values()), default=str)
        self.assertNotIn(first.data["token"], stored)
        self.assertNotIn(WECHAT_KEY, stored)
        self.assertEqual(decrypt_wechat_session_key(session), WECHAT_KEY)

    def test_each_device_retains_its_own_wechat_key(self):
        first = self.login()
        first_session = StudentSession.objects.get(token_digest=hashlib.sha256(first.data["token"].encode()).hexdigest())
        another_key = base64.b64encode(b"second-devicekey").decode()
        self.login(WeChatLogin(LOGIN.app_id, LOGIN.openid, another_key))
        first_session.refresh_from_db()
        self.assertEqual(decrypt_wechat_session_key(first_session), WECHAT_KEY)
        self.assertEqual(decrypt_wechat_session_key(WeChatIdentity.objects.get()), another_key)

    def test_valid_bearer_and_staff_session_are_isolated(self):
        token = self.login().data["token"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)
        self.assertEqual(self.client.get("/api/student/v1/probe/private/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/questions/").status_code, 403)
        self.client.credentials()
        staff = User.objects.create_superuser(username="staff", password="test-admin-password")
        self.client.force_login(staff)
        self.assertEqual(self.client.get("/api/student/v1/probe/private/").status_code, 401)
        self.assertTrue(self.client.get("/api/student/v1/probe/public/").data["anonymous"])

    def test_invalid_headers_never_fall_back_to_anonymous(self):
        self.assertEqual(self.client.get("/api/student/v1/probe/public/").status_code, 200)
        for header in ("", " ", "Basic abc", "Bearer", "Bearer bad", "Bearer " + "a" * 43, "Bearer 中文"):
            with self.subTest(header=header):
                self.client.credentials(HTTP_AUTHORIZATION=header)
                response = self.client.get("/api/student/v1/probe/public/")
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.data["error"]["code"], "INVALID_TOKEN")
                self.assertEqual(response.data["requestId"], response["X-Request-ID"])

    def test_expired_revoked_inactive_and_staff_sessions_rejected(self):
        token = self.login().data["token"]
        session = StudentSession.objects.get()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)
        original_expiry = session.expires_at
        for change in (
            {"expires_at": timezone.now() - timedelta(seconds=1)},
            {"expires_at": original_expiry, "revoked_at": timezone.now()},
        ):
            StudentSession.objects.filter(pk=session.pk).update(**change)
            self.assertEqual(self.client.get("/api/student/v1/probe/public/").status_code, 401)
        StudentSession.objects.filter(pk=session.pk).update(revoked_at=None)
        User.objects.filter(pk=session.user_id).update(is_active=False)
        self.assertEqual(self.client.get("/api/student/v1/probe/public/").status_code, 401)
        User.objects.filter(pk=session.user_id).update(is_active=True, is_staff=True)
        self.assertEqual(self.client.get("/api/student/v1/probe/public/").status_code, 401)

    def test_logout_revokes_only_current_session_and_disable_revokes_all(self):
        tokens = [self.login().data["token"], self.login().data["token"]]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens[0])
        self.assertEqual(self.client.post("/api/student/v1/auth/logout/").status_code, 204)
        self.assertEqual(self.client.post("/api/student/v1/auth/logout/").status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens[1])
        self.assertEqual(self.client.get("/api/student/v1/probe/private/").status_code, 200)
        user = User.objects.get()
        user.is_active = False
        user.save(update_fields=("is_active",))
        self.assertFalse(StudentSession.objects.filter(revoked_at__isnull=True).exists())
        user.is_active = True
        user.save(update_fields=("is_active",))
        self.assertEqual(self.client.get("/api/student/v1/probe/public/").status_code, 401)
        self.client.credentials()
        self.assertEqual(self.login().status_code, 200)

    def test_disabled_identity_cannot_login_and_has_no_new_sessions(self):
        self.login()
        user = User.objects.get()
        user.is_active = False
        user.save()
        response = self.login()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "STUDENT_DISABLED")
        self.assertEqual(StudentSession.objects.count(), 1)

    def test_untrusted_identity_fields_and_form_inputs_are_rejected(self):
        for extra in ("openid", "appid", "session_key"):
            response = self.client.post(self.url, {"code": "test-code", extra: "fake"}, format="json")
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post(self.url, {"code": "test-code"}, format="multipart").status_code, 415)
        self.assertFalse(User.objects.exists())

    def test_missing_encryption_config_does_not_leave_identity_or_user(self):
        with override_settings(STUDENT_SESSION_ENCRYPTION_KEYS=()):
            response = self.login()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(User.objects.exists())
        self.assertFalse(WeChatIdentity.objects.exists())
        self.assertEqual(RateBucket.objects.get().count, 1)

    def test_failed_login_rate_limits_are_committed_and_proxy_headers_cannot_bypass(self):
        failure = BusinessError("WECHAT_CODE_INVALID", "凭证无效", 401)
        with override_settings(WECHAT_LOGIN_RATE_LIMIT=2):
            with patch("accounts.student_api.exchange_code", side_effect=failure) as exchange:
                for index in range(2):
                    response = self.client.post(self.url, {"code": "bad"}, format="json", HTTP_X_REAL_IP=f"192.0.2.{index}")
                    self.assertEqual(response.status_code, 401)
                response = self.client.post(self.url, {"code": "bad"}, format="json", HTTP_X_REAL_IP="192.0.2.99")
                self.assertEqual(response.status_code, 429)
                self.assertEqual(exchange.call_count, 2)
        self.assertEqual(RateBucket.objects.get().count, 2)
        self.assertFalse(User.objects.exists())

    def test_rotation_reencrypts_identity_and_device_without_plaintext_output(self):
        _, session = issue_student_session(LOGIN)
        original = session.encrypted_session_key
        new_key = Fernet.generate_key().decode()
        output = StringIO()
        with override_settings(STUDENT_SESSION_ENCRYPTION_KEYS=(new_key, TEST_KEY)):
            call_command("rotate_wechat_session_keys", stdout=output)
        session.refresh_from_db()
        self.assertNotEqual(session.encrypted_session_key, original)
        with override_settings(STUDENT_SESSION_ENCRYPTION_KEYS=(new_key,)):
            self.assertEqual(decrypt_wechat_session_key(session), WECHAT_KEY)
            self.assertEqual(decrypt_wechat_session_key(WeChatIdentity.objects.get()), WECHAT_KEY)
        self.assertNotIn(WECHAT_KEY, output.getvalue())

    def test_rotation_dry_run_and_failed_batch_leave_ciphertexts_unchanged(self):
        issue_student_session(LOGIN)
        second = WeChatLogin(LOGIN.app_id, "another-openid", WECHAT_KEY)
        issue_student_session(second)
        original = WeChatIdentity.objects.first().encrypted_session_key
        call_command("rotate_wechat_session_keys", dry_run=True, stdout=StringIO())
        self.assertEqual(WeChatIdentity.objects.first().encrypted_session_key, original)
        WeChatIdentity.objects.filter(openid=second.openid).update(encrypted_session_key="broken")
        with override_settings(STUDENT_SESSION_ENCRYPTION_KEYS=(Fernet.generate_key().decode(), TEST_KEY)):
            with self.assertRaises(CommandError):
                call_command("rotate_wechat_session_keys", stdout=StringIO())
        self.assertEqual(WeChatIdentity.objects.first().encrypted_session_key, original)

    def test_admin_never_exposes_token_or_wechat_keys_and_identity_is_readonly(self):
        raw_token, session = issue_student_session(LOGIN)
        identity = WeChatIdentity.objects.get()
        staff = User.objects.create_superuser(username="root", password="test-admin-password")
        self.client.force_login(staff)
        for model, instance in (("wechatidentity", identity), ("studentsession", session)):
            url = reverse(f"admin:accounts_{model}_change", args=(instance.pk,))
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, WECHAT_KEY)
            self.assertNotContains(response, raw_token)
            self.assertNotContains(response, instance.encrypted_session_key)
            self.assertEqual(self.client.post(url, {"user": staff.pk}).status_code, 403)


@override_settings(WECHAT_APP_ID="wx-test-app", WECHAT_APP_SECRET="server-secret", WECHAT_LOGIN_TIMEOUT_SECONDS=5)
class WeChatAdapterTests(TestCase):
    def adapter(self, payload=None, raw=None):
        response = MagicMock()
        response.read.return_value = raw if raw is not None else json.dumps(payload).encode()
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        return opener

    def test_request_uses_only_server_credentials_and_fixed_endpoint(self):
        opener = self.adapter({"openid": LOGIN.openid, "session_key": WECHAT_KEY})
        with patch("accounts.wechat.build_opener", return_value=opener):
            result = exchange_code("new-code")
        request = opener.open.call_args.args[0]
        self.assertTrue(request.full_url.startswith("https://api.weixin.qq.com/sns/jscode2session?"))
        self.assertIn("appid=wx-test-app", request.full_url)
        self.assertIn("js_code=new-code", request.full_url)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 5)
        self.assertEqual(result, LOGIN)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.example/"))

    def test_missing_configuration_does_not_call_external_platform(self):
        with override_settings(WECHAT_APP_SECRET=""):
            with patch("accounts.wechat.build_opener") as opener:
                with self.assertRaises(BusinessError) as error:
                    exchange_code("new-code")
                self.assertEqual(error.exception.status_code, 503)
                opener.assert_not_called()

    def test_bad_code_is_auth_error_and_other_platform_failures_are_unavailable(self):
        for error_code, expected in ((40029, 401), (40163, 401), (45011, 503), (-1, 503)):
            with self.subTest(error_code=error_code):
                opener = self.adapter({"errcode": error_code, "errmsg": "secret-external-text"})
                with patch("accounts.wechat.build_opener", return_value=opener):
                    with self.assertRaises(BusinessError) as error:
                        exchange_code("bad-code")
                self.assertEqual(error.exception.status_code, expected)
                self.assertNotIn("secret-external-text", str(error.exception))

    def test_timeout_malformed_and_oversized_responses_never_leak_external_secrets(self):
        cases = (b"invalid-json", b"x" * (64 * 1024 + 1), b"[]", b"{}", b'{"openid":"ok","session_key":"bad!"}')
        for raw in cases:
            with patch("accounts.wechat.build_opener", return_value=self.adapter(raw=raw)):
                with self.assertRaises(BusinessError) as error:
                    exchange_code("code")
                self.assertEqual(error.exception.status_code, 503)
        opener = MagicMock()
        opener.open.side_effect = URLError("secret-url-and-code")
        with patch("accounts.wechat.build_opener", return_value=opener):
            with self.assertRaises(BusinessError) as error:
                exchange_code("code")
        self.assertNotIn("secret-url-and-code", str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)
