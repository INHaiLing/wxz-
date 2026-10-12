from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from feedback.models import Feedback


@override_settings(STUDENT_SESSION_ENCRYPTION_KEYS=(Fernet.generate_key().decode(),))
class FeedbackAdminTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.student = user_model.objects.create_user(username="feedback-admin-student")
        self.feedback = Feedback.objects.create(
            user=self.student,
            feedback_type="question",
            content="这道题的答案似乎有误。",
            contact="feedback-contact",
        )
        self.operator = user_model.objects.create_user(username="feedback-operator", is_staff=True)
        self.operator.user_permissions.add(
            Permission.objects.get(content_type__app_label="feedback", codename="view_feedback"),
            Permission.objects.get(content_type__app_label="feedback", codename="process_feedback"),
        )
        self.viewer = user_model.objects.create_user(username="feedback-viewer", is_staff=True)
        self.viewer.user_permissions.add(
            Permission.objects.get(content_type__app_label="feedback", codename="view_feedback")
        )
        self.client = Client()

    def test_operator_can_view_and_process_feedback_with_audit_fields(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("admin:feedback_feedback_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "这道题的答案似乎有误")
        response = self.client.post(
            reverse("admin:feedback_feedback_change", args=(self.feedback.pk,)),
            {"status": "processing", "admin_note": "已转交题库管理员。", "_save": "保存"},
        )
        self.assertEqual(response.status_code, 302)
        self.feedback.refresh_from_db()
        self.assertEqual(self.feedback.status, "processing")
        self.assertEqual(self.feedback.admin_note, "已转交题库管理员。")
        self.assertEqual(self.feedback.handled_by_id, self.operator.pk)
        self.assertIsNotNone(self.feedback.handled_at)

    def test_viewer_can_read_but_cannot_process_or_add_delete(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("admin:feedback_feedback_changelist")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin:feedback_feedback_change", args=(self.feedback.pk,))).status_code,
            200,
        )
        response = self.client.post(
            reverse("admin:feedback_feedback_change", args=(self.feedback.pk,)),
            {"status": "resolved", "admin_note": "不应被保存。", "_save": "保存"},
        )
        self.assertEqual(response.status_code, 403)
        self.feedback.refresh_from_db()
        self.assertEqual(self.feedback.status, "pending")
        self.assertEqual(self.feedback.admin_note, "")
