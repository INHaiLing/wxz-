import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import StudentSession
from content.models import LearningConfiguration
from entitlements.services import grant_entitlement

from writing.models import WritingCategory, WritingContent
from writing.services import (
    ContentConflict,
    make_draft_token,
    make_publication_token,
    publish_writings,
    save_writing_draft,
    unpublish_writings,
)
from .test_access import WritingFixture


@override_settings(ROOT_URLCONF="writing.tests.urls")
class WritingAPITests(WritingFixture):
    def setUp(self):
        self.client = APIClient()
        self.student = get_user_model().objects.create_user(username="writing-student")
        self.operator = get_user_model().objects.create_superuser(username="writing-operator", password="test-only-writing")
        self.token = secrets.token_urlsafe(32)
        StudentSession.objects.create(
            user=self.student, token_digest=hashlib.sha256(self.token.encode()).hexdigest(),
            encrypted_session_key="test-only-ciphertext",
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.list_url = "/api/student/v1/writings/?categoryId=notice"

    def authenticate(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token)

    def test_anonymous_list_hides_paid_body_and_kind_filter_does_not_add_free_content(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data["results"]], ["w-01", "w-02"])
        self.assertEqual(response.data["access"], {
            "activated": False, "pageSize": 2, "freeCount": 2, "totalCount": 3, "restrictedCount": 1,
        })
        exercise = self.client.get(self.list_url + "&kind=exercise")
        self.assertEqual(exercise.status_code, 200)
        self.assertEqual(exercise.data["results"], [])
        self.assertEqual(exercise.data["access"]["freeCount"], 0)
        paid_detail = self.client.get("/api/student/v1/writings/w-03/")
        self.assertEqual(paid_detail.status_code, 403)
        self.assertNotIn("w-03正文", str(paid_detail.data))
        self.assertNotIn("参考答案", str(paid_detail.data))

    def test_free_detail_and_exercise_reference_answer(self):
        detail = self.client.get("/api/student/v1/writings/w-01/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["body"], "w-01正文\n第二行")
        self.assertTrue(detail.data["access"]["free"])
        self.authenticate()
        grant_entitlement(self.student, "activation", "writing-test", actor=self.operator)
        exercise = self.client.get("/api/student/v1/writings/w-03/")
        self.assertEqual(exercise.status_code, 200)
        self.assertEqual(exercise.data["referenceAnswer"], "参考答案")

    def test_draft_edit_stays_hidden_until_republish_then_unpublish_hides_it(self):
        self.first.body = "草稿新正文"
        self.first.save()
        self.assertEqual(self.client.get("/api/student/v1/writings/w-01/").data["body"], "w-01正文\n第二行")
        contents = WritingContent.objects.filter(pk="w-01")
        token = make_publication_token(list(contents), self.operator, "publish_selected")
        publish_writings(contents, self.operator, confirmation_token=token)
        self.assertEqual(self.client.get("/api/student/v1/writings/w-01/").data["body"], "草稿新正文")
        contents = WritingContent.objects.filter(pk="w-01")
        token = make_publication_token(list(contents), self.operator, "unpublish_selected")
        unpublish_writings(contents, self.operator, confirmation_token=token)
        self.assertEqual(self.client.get("/api/student/v1/writings/w-01/").status_code, 404)

    def test_invalid_queries_and_inactive_category_are_rejected(self):
        for url in (
            self.list_url + "&kind=unknown",
            self.list_url + "&page=0",
            self.list_url + "&unexpected=1",
            self.list_url + "&kind=template&kind=example",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 400)
        self.assertEqual(self.client.get("/api/student/v1/writings/not-found/").status_code, 404)
        WritingCategory.objects.filter(pk=self.category.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.list_url).status_code, 404)

    def test_page_size_change_applies_without_cross_kind_leak(self):
        LearningConfiguration.objects.filter(pk=1).update(page_size=1)
        response = self.client.get(self.list_url)
        self.assertEqual([item["id"] for item in response.data["results"]], ["w-01"])
        self.assertEqual(response.data["access"]["restrictedCount"], 2)

    def test_publication_confirmation_audits_and_rejects_stale_confirmation(self):
        draft = WritingContent.objects.get(pk="w-draft")
        self.client.force_login(self.operator)
        changelist = reverse("admin:writing_writingcontent_changelist")
        payload = {"action": "publish_selected", "_selected_action": [draft.pk]}
        preview = self.client.post(changelist, payload)
        self.assertEqual(preview.status_code, 200)
        token = preview.context["confirmation_token"]

        draft.body = "changed after preview"
        draft.save()
        stale = self.client.post(changelist, {
            **payload, "confirm_publication": "publish_selected", "confirmation_token": token,
        })
        self.assertEqual(stale.status_code, 302)
        self.assertFalse(WritingContent.objects.get(pk=draft.pk).is_published)
        self.assertFalse(LogEntry.objects.filter(object_id=draft.pk).exists())

        preview = self.client.post(changelist, payload)
        token = preview.context["confirmation_token"]
        response = self.client.post(changelist, {
            **payload, "confirm_publication": "publish_selected", "confirmation_token": token,
        })
        self.assertEqual(response.status_code, 302)
        published = WritingContent.objects.get(pk=draft.pk)
        self.assertTrue(published.is_published)
        self.assertEqual(published.published_revision.created_by_id, self.operator.pk)
        self.assertTrue(LogEntry.objects.filter(object_id=draft.pk).exists())

    def test_draft_edit_requires_permission_and_fresh_editor_token(self):
        editor = get_user_model().objects.create_user(username="writing-editor", is_staff=True)
        permission = Permission.objects.get(content_type__app_label="writing", codename="change_writingcontent")
        editor.user_permissions.add(permission)
        content = WritingContent.objects.get(pk="w-draft")
        token = make_draft_token(content, editor)
        content.title = "新标题"
        save_writing_draft(content, editor, token)
        self.assertEqual(WritingContent.objects.get(pk=content.pk).title, "新标题")

        changed = WritingContent.objects.get(pk=content.pk)
        changed.title = "覆盖标题"
        with self.assertRaises(ContentConflict):
            save_writing_draft(changed, editor, token)

        no_permission = get_user_model().objects.create_user(username="writing-no-permission", is_staff=True)
        with self.assertRaises(PermissionDenied):
            save_writing_draft(changed, no_permission, make_draft_token(changed, no_permission))
