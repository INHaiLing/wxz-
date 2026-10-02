from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from .errors import BusinessError, student_exception_handler
from .idempotency import lookup, remember
from .limits import check_rate, client_ip
from .models import RateBucket
from pathlib import Path
from tempfile import TemporaryDirectory
from scripts.initialize_env import create_local_env


class InfrastructureTests(TestCase):
    def test_setup_creates_random_keys_once_and_preserves_existing_credentials(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)
            (path / ".env.example").write_text(
                "DJANGO_SECRET_KEY=replace-with-generated-secret-at-least-32-characters\n"
                "STUDENT_SESSION_ENCRYPTION_KEYS=\n", encoding="utf-8")
            self.assertTrue(create_local_env(path))
            original = (path / ".env").read_text(encoding="utf-8")
            self.assertNotIn("replace-with", original)
            self.assertNotIn("STUDENT_SESSION_ENCRYPTION_KEYS=\n", original)
            self.assertFalse(create_local_env(path))
            self.assertEqual((path / ".env").read_text(encoding="utf-8"), original)

    def test_replay_is_bound_to_actor_operation_and_payload(self):
        user = get_user_model().objects.create_user("test-infra")
        other = get_user_model().objects.create_user("test-infra-other")
        remember(user, "redeem", "request-0001", {"codeDigest": "a"}, {"id": "1"})
        self.assertEqual(lookup(user, "redeem", "request-0001", {"codeDigest": "a"}).response, {"id": "1"})
        self.assertIsNone(lookup(other, "redeem", "request-0001", {"codeDigest": "a"}))
        self.assertIsNone(lookup(user, "purchase", "request-0001", {"codeDigest": "a"}))
        with self.assertRaises(BusinessError):
            lookup(user, "redeem", "request-0001", {"codeDigest": "b"})

    def test_shared_rate_limit_contains_no_raw_subject(self):
        check_rate("test", "private-ip", 1)
        with self.assertRaises(BusinessError) as error:
            check_rate("test", "private-ip", 1)
        self.assertEqual(error.exception.status_code, 429)
        self.assertNotEqual(RateBucket.objects.get().subject_digest, "private-ip")

    def test_student_error_does_not_change_global_handler(self):
        request = APIRequestFactory().get("/")
        request.request_id = "test-request-id"
        result = student_exception_handler(BusinessError("ALREADY_ACTIVATED", "已激活", 409), {"request": request})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.data["error"]["code"], "ALREADY_ACTIVATED")
        self.assertEqual(result.data["requestId"], "test-request-id")

    def test_untrusted_forwarded_address_cannot_evade_rate_limit(self):
        request = APIRequestFactory().get("/", REMOTE_ADDR="192.0.2.5", HTTP_X_REAL_IP="198.51.100.10")
        self.assertEqual(client_ip(request), "192.0.2.5")
        with override_settings(TRUSTED_PROXY_IPS=["192.0.2.5"]):
            self.assertEqual(client_ip(request), "198.51.100.10")
        request.META["HTTP_X_REAL_IP"] = "forged-value"
        with override_settings(TRUSTED_PROXY_IPS=["192.0.2.5"]):
            self.assertEqual(client_ip(request), "192.0.2.5")
