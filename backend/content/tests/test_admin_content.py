from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from content.forms import AnswerListField, QuestionAdminForm
from content.models import Category, Question, QuestionRevision
from content.services import publish_questions


class ContentAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(id="pre-qin", name="先秦文学")
        cls.question = Question.objects.create(
            id="lit-shijing", source="literature", type="fact", category=cls.category,
            stem="《诗经》收录{{0}}篇。", answers=["305"],
        )
        user_model = get_user_model()
        cls.editor = user_model.objects.create_user(username="admin-editor", is_staff=True)
        cls.editor.user_permissions.set(Permission.objects.filter(
            content_type__app_label="content", codename__in=("view_question", "change_question")
        ))
        cls.publisher = user_model.objects.create_user(username="admin-publisher", is_staff=True)
        cls.publisher.user_permissions.set(Permission.objects.filter(
            content_type__app_label="content", codename__in=("view_question", "publish_question")
        ))
        cls.no_permissions = user_model.objects.create_user(username="admin-no-permissions", is_staff=True)
        cls.superuser = user_model.objects.create_superuser(username="admin-root", password="test-admin-secret-17")

    def setUp(self):
        self.list_url = reverse("admin:content_question_changelist")
        self.change_url = reverse("admin:content_question_change", args=(self.question.pk,))
        self.preview_url = reverse("admin:content_question_preview", args=(self.question.pk,))

    def publish_post(self, confirmed=False):
        data = {"action": "publish_selected", "_selected_action": [self.question.pk]}
        if confirmed:
            preview = self.client.post(self.list_url, data)
            data["confirmation_token"] = preview.context["confirmation_token"]
            data["confirm_publication"] = "publish_selected"
        return self.client.post(self.list_url, data)

    def test_publication_action_requires_confirmation_and_permission(self):
        self.client.force_login(self.publisher)
        confirmation = self.publish_post()
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "确认发布题目")
        self.assertFalse(Question.objects.get(pk=self.question.pk).is_published)
        result = self.publish_post(confirmed=True)
        self.assertEqual(result.status_code, 302)
        self.assertTrue(Question.objects.get(pk=self.question.pk).is_published)
        self.assertEqual(QuestionRevision.objects.count(), 1)

        self.client.force_login(self.editor)
        response = self.client.post(self.list_url, {
            "action": "unpublish_selected", "_selected_action": [self.question.pk],
            "confirm_publication": "unpublish_selected",
        })
        self.assertIn(response.status_code, (200, 302))
        self.assertTrue(Question.objects.get(pk=self.question.pk).is_published)

    def test_preview_requires_view_permission_and_escapes_content(self):
        self.assertEqual(self.client.get(self.preview_url).status_code, 302)
        self.client.force_login(self.no_permissions)
        self.assertEqual(self.client.get(self.preview_url).status_code, 403)
        self.client.force_login(self.editor)
        self.question.stem = "<script>alert(1)</script>答案是{{0}}。"
        self.question.save()
        response = self.client.get(self.preview_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "默写 · 回忆答案")
        self.assertContains(response, "背诵 · 查看答案")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")

    def test_draft_edit_cannot_change_identity_or_publication_fields_and_is_logged(self):
        publish_questions(Question.objects.filter(pk=self.question.pk), self.publisher)
        self.question.refresh_from_db()
        revision_id = self.question.published_revision_id
        self.client.force_login(self.editor)
        token = self.client.get(self.change_url).context["adminform"].form["draft_token"].value()
        response = self.client.post(self.change_url, {
            "id": "try-to-replace-id", "source": "literature", "category": self.category.pk,
            "article": "", "type": "fact", "tag": "修订", "sort_order": "0",
            "stem": "《诗经》又称{{0}}。", "answers": "诗三百",
            "is_published": "", "published_revision": "", "_save": "保存",
            "draft_token": token,
        })
        self.assertEqual(response.status_code, 302)
        self.question.refresh_from_db()
        self.assertEqual(self.question.answers, ["诗三百"])
        self.assertEqual(self.question.pk, "lit-shijing")
        self.assertEqual(self.question.published_revision_id, revision_id)
        self.assertTrue(self.question.is_published)
        self.assertEqual(self.question.published_revision.answers, ["305"])
        self.assertFalse(Question.objects.filter(pk="try-to-replace-id").exists())
        self.assertTrue(LogEntry.objects.filter(
            user=self.editor, object_id=self.question.pk, action_flag=CHANGE
        ).exists())

    def test_publisher_can_view_but_cannot_save_drafts(self):
        self.client.force_login(self.publisher)
        self.assertEqual(self.client.get(self.change_url).status_code, 200)
        self.assertEqual(self.client.post(self.change_url, {"stem": "越权{{0}}"}).status_code, 403)

    def test_history_and_delete_routes_are_read_only_even_for_superuser(self):
        publish_questions(Question.objects.filter(pk=self.question.pk), self.publisher)
        revision = QuestionRevision.objects.get(question=self.question)
        self.client.force_login(self.superuser)
        revision_url = reverse("admin:content_questionrevision_change", args=(revision.pk,))
        self.assertEqual(self.client.get(revision_url).status_code, 200)
        self.assertEqual(self.client.post(revision_url, {"stem": "越权{{0}}"}).status_code, 403)
        delete_url = reverse("admin:content_question_delete", args=(self.question.pk,))
        self.assertEqual(self.client.get(delete_url).status_code, 403)

    def test_mutating_admin_actions_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.publisher)
        response = client.post(self.list_url, {
            "action": "publish_selected", "_selected_action": [self.question.pk],
            "confirm_publication": "publish_selected",
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Question.objects.get(pk=self.question.pk).is_published)

    def test_answers_form_supports_plain_lines_and_lossless_multiline_json(self):
        form = QuestionAdminForm(data={
            "id": "new-question", "source": "literature", "category": self.category.pk,
            "type": "fact", "stem": "{{0}}与{{1}}。", "answers": "风\n雅", "sort_order": 0,
        })
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data["answers"], ["风", "雅"])
        field = AnswerListField()
        answers = ["第一行\n第二行", "另一项"]
        self.assertEqual(field.clean(field.prepare_value(answers)), answers)
        question = form.save()
        self.assertEqual(question.answers, ["风", "雅"])
