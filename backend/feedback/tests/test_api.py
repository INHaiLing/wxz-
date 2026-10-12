import base64

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.services import issue_student_session
from accounts.wechat import WeChatLogin
from common.models import IdempotencyRecord

from feedback.models import Feedback


@override_settings(STUDENT_SESSION_ENCRYPTION_KEYS=(Fernet.generate_key().decode(),))
class FeedbackAPITests(TestCase):
    url = "/api/student/v1/feedback/"

    def setUp(self):
        self.client = APIClient()
        self.token, session = issue_student_session(
            WeChatLogin("feedback-app", "feedback-student", base64.b64encode(b"feedback-key").decode())
        )
        self.student = session.user
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token)

    def post(self, payload=None, key="feedback-key-01"):
        return self.client.post(
            self.url,
            payload if payload is not None else {"type": "feature", "content": "希望增加夜间模式。", "contact": "wx-feedback"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def test_requires_student_bearer_and_does_not_offer_history(self):
        self.client.credentials()
        self.assertEqual(self.post().status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
        self.assertEqual(self.post().status_code, 401)
        staff = get_user_model().objects.create_user(username="feedback-cookie-staff", password="cookie-password", is_staff=True)
        self.client.credentials()
        self.assertTrue(self.client.login(username=staff.username, password="cookie-password"))
        self.assertEqual(self.post().status_code, 401)

    def test_valid_submission_associates_authenticated_student(self):
        response = self.post()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.data), {"id", "type", "status", "createdAt"})
        self.assertEqual(response.data["type"], "feature")
        self.assertEqual(response.data["status"], "pending")
        self.assertFalse("希望增加夜间模式" in str(response.data))
        feedback = Feedback.objects.get(pk=response.data["id"])
        self.assertEqual(feedback.user_id, self.student.pk)
        self.assertEqual(feedback.contact, "wx-feedback")

    def test_validation_rejects_unknown_fields_invalid_type_and_blank_content(self):
        payloads = (
            {},
            {"type": "unknown", "content": "内容"},
            {"type": "feature", "content": "   "},
            {"type": "feature", "content": "内容", "extra": True},
            {"type": "feature", "content": "内容", "userId": 999},
            {"type": "feature", "content": "x" * 5001},
            {"type": "feature", "content": "内容", "contact": "x" * 201},
        )
        for payload in payloads:
            with self.subTest(payload_keys=tuple(payload)):
                self.assertEqual(self.post(payload).status_code, 400)
        self.assertEqual(self.post({"type": "feature", "content": "内容"}, key="short").status_code, 400)
        self.assertEqual(Feedback.objects.count(), 0)

    def test_idempotent_retry_returns_original_result_without_duplicate(self):
        first = self.post()
        replay = self.post({"type": "feature", "content": "希望增加夜间模式。", "contact": "wx-feedback"})
        self.assertEqual(first.status_code, replay.status_code)
        self.assertEqual(first.data, replay.data)
        self.assertEqual(Feedback.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.filter(operation="feedback.submit").count(), 1)

    def test_reusing_idempotency_key_for_different_payload_is_conflict(self):
        self.assertEqual(self.post().status_code, 201)
        response = self.post({"type": "payment", "content": "支付页面没有加载。"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(Feedback.objects.count(), 1)

    def test_idempotency_keys_are_scoped_to_the_authenticated_student(self):
        self.assertEqual(self.post(key="shared-key-01").status_code, 201)
        other_token, other_session = issue_student_session(
            WeChatLogin("feedback-app", "feedback-other-key", base64.b64encode(b"feedback-key-3").decode())
        )
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + other_token)
        response = self.post({"type": "other", "content": "另一位学员的反馈。"}, key="shared-key-01")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Feedback.objects.filter(user=other_session.user).count(), 1)
        self.assertEqual(Feedback.objects.count(), 2)

    def test_students_have_no_feedback_history_or_detail_endpoint(self):
        self.post()
        other_token, other_session = issue_student_session(
            WeChatLogin("feedback-app", "feedback-other", base64.b64encode(b"feedback-key-2").decode())
        )
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + other_token)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.get(self.url + "1/").status_code, 404)
        self.assertEqual(Feedback.objects.filter(user=other_session.user).count(), 0)
