import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.db import connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature

from accounts.models import StudentSession, User, WeChatIdentity
from accounts.services import decrypt_wechat_session_key, issue_student_session
from accounts.wechat import WeChatLogin


@skipUnlessDBFeature("has_select_for_update")
@override_settings(STUDENT_SESSION_ENCRYPTION_KEYS=(Fernet.generate_key().decode(),))
class StudentIdentityConcurrencyTests(TransactionTestCase):
    def test_first_logins_race_without_orphan_users_or_mixed_device_keys(self):
        barrier = Barrier(2)
        create_user = User.objects.create_user

        def concurrent_candidate(*args, **kwargs):
            user = create_user(*args, **kwargs)
            barrier.wait(timeout=10)
            return user

        def login(index):
            try:
                return issue_student_session(WeChatLogin(
                    "wx-concurrent-app", "same-openid",
                    base64.b64encode(f"device-{index}".encode()).decode(),
                ))
            finally:
                connections.close_all()

        with patch("accounts.services.User.objects.create_user", side_effect=concurrent_candidate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(login, index) for index in range(2)]
                results = [future.result(timeout=20) for future in futures]
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(WeChatIdentity.objects.count(), 1)
        self.assertEqual(StudentSession.objects.count(), 2)
        self.assertNotEqual(results[0][0], results[1][0])
        self.assertEqual(
            {decrypt_wechat_session_key(session) for _, session in results},
            {base64.b64encode(b"device-0").decode(), base64.b64encode(b"device-1").decode()},
        )
