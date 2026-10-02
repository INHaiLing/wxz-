"""Actual admin requests for optimistic draft and publication confirmation."""

from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase
from django.urls import reverse

from content.models import Category, Question, QuestionRevision
from content.services import (
    CONFIRMATION_SECONDS, ContentConflict, make_draft_token,
    make_publication_token, publish_questions, save_question_draft,
)


class AdminConflictTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(id="conflict", name="并发验证")
        cls.question = Question.objects.create(
            id="conflict-q", source="literature", type="fact", category=cls.category,
            stem="原答案{{0}}", answers=["原值"],
        )
        cls.other = Question.objects.create(
            id="conflict-other", source="literature", type="fact", category=cls.category,
            stem="另一题{{0}}", answers=["另一值"],
        )
        users = get_user_model()
        cls.editor = users.objects.create_user(username="conflict-editor", is_staff=True)
        cls.another = users.objects.create_user(username="conflict-another", is_staff=True)
        permissions = Permission.objects.filter(
            content_type__app_label="content",
            codename__in=("view_question", "change_question", "publish_question"),
        )
        cls.editor.user_permissions.set(permissions)
        cls.another.user_permissions.set(permissions)

    def setUp(self):
        self.client.force_login(self.editor)
        self.change_url = reverse("admin:content_question_change", args=(self.question.pk,))
        self.list_url = reverse("admin:content_question_changelist")

    def get_edit_token(self):
        response = self.client.get(self.change_url)
        self.assertEqual(response.status_code, 200)
        return response.context["adminform"].form["draft_token"].value()

    def edit(self, token, answer="修改值"):
        data = {
            "source": "literature", "category": self.category.pk, "article": "",
            "type": "fact", "tag": "", "sort_order": "0",
            "stem": "改后答案{{0}}", "answers": answer, "_save": "保存",
        }
        if token is not None:
            data["draft_token"] = token
        return self.client.post(self.change_url, data)

    def confirmation(self, action="publish_selected", ids=None):
        ids = ids or [self.question.pk]
        response = self.client.post(self.list_url, {"action": action, "_selected_action": ids})
        self.assertEqual(response.status_code, 200)
        return response.context["confirmation_token"]

    def confirm(self, token, action="publish_selected", ids=None, **extra):
        data = {
            "action": action, "confirm_publication": action,
            "_selected_action": ids or [self.question.pk], **extra,
        }
        if token is not None:
            data["confirmation_token"] = token
        return self.client.post(self.list_url, data)

    def test_old_form_is_409_and_preserves_latest_draft_and_submitted_text(self):
        token = self.get_edit_token()
        self.assertEqual(self.edit(token, "先保存").status_code, 302)
        response = self.edit(token, "旧表单文本")
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "重新打开", status_code=409)
        self.assertEqual(response.context["adminform"].form["answers"].value(), "旧表单文本")
        self.question.refresh_from_db()
        self.assertEqual(self.question.answers, ["先保存"])

    def test_missing_tampered_other_user_other_question_and_expired_draft_tokens(self):
        token = self.get_edit_token()
        for bad_token in (
            None, token + "tampered", make_draft_token(self.question, self.another),
            make_draft_token(self.other, self.editor),
        ):
            with self.subTest(token=bad_token):
                self.assertEqual(self.edit(bad_token).status_code, 409)
        with patch("django.core.signing.time.time", return_value=100):
            expired = make_draft_token(self.question, self.editor)
        with patch("django.core.signing.time.time", return_value=100 + CONFIRMATION_SECONDS + 1):
            self.assertEqual(self.edit(expired).status_code, 409)
        self.question.refresh_from_db()
        self.assertEqual(self.question.answers, ["原值"])
        self.assertEqual(LogEntry.objects.count(), 0)

    def test_publication_during_edit_is_preserved(self):
        token = self.get_edit_token()
        publish_questions(Question.objects.filter(pk=self.question.pk), self.editor)
        self.question.refresh_from_db()
        revision_id = self.question.published_revision_id
        self.assertEqual(self.edit(token).status_code, 302)
        self.question.refresh_from_db()
        self.assertTrue(self.question.is_published)
        self.assertEqual(self.question.published_revision_id, revision_id)
        self.assertEqual(self.question.published_revision.answers, ["原值"])

    def test_save_time_conflict_is_a_form_response_not_500(self):
        token = self.get_edit_token()
        def partial_save_then_conflict(*args, **kwargs):
            Question.objects.filter(pk=self.question.pk).update(tag="不应保留的部分写入")
            raise ContentConflict("保存时发生冲突，请重新打开题目。")

        with patch("content.admin.save_question_draft", side_effect=partial_save_then_conflict):
            response = self.edit(token)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "保存时发生冲突", status_code=409)
        self.question.refresh_from_db()
        self.assertEqual(self.question.answers, ["原值"])
        self.assertEqual(self.question.tag, "")

    def test_confirmation_draft_change_rejects_whole_batch(self):
        ids = [self.question.pk, self.other.pk]
        token = self.confirmation(ids=ids)
        self.other.answers = ["确认期间修改"]
        self.other.save()
        response = self.confirm(token, ids=ids)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "重新确认", status_code=409)
        self.assertEqual(Question.objects.filter(is_published=True).count(), 0)
        self.assertEqual(QuestionRevision.objects.count(), 0)
        self.assertEqual(LogEntry.objects.count(), 0)

    def test_missing_tampered_expired_changed_set_user_action_and_select_across(self):
        token = self.confirmation()
        attacks = (
            (None, "publish_selected", [self.question.pk], {}),
            (token + "tampered", "publish_selected", [self.question.pk], {}),
            (token, "publish_selected", [self.question.pk, self.other.pk], {}),
            (token, "unpublish_selected", [self.question.pk], {}),
            (token, "publish_selected", [self.question.pk], {"select_across": "1"}),
            (make_publication_token([self.question], self.another, "publish_selected"), "publish_selected", [self.question.pk], {}),
        )
        for bad_token, action, ids, extra in attacks:
            with self.subTest(action=action, token=bad_token):
                self.assertEqual(self.confirm(bad_token, action, ids, **extra).status_code, 409)
        with patch("django.core.signing.time.time", return_value=100):
            expired = make_publication_token([self.question], self.editor, "publish_selected")
        with patch("django.core.signing.time.time", return_value=100 + CONFIRMATION_SECONDS + 1):
            self.assertEqual(self.confirm(expired).status_code, 409)
        self.assertEqual(QuestionRevision.objects.count(), 0)

    def test_confirmation_binds_published_state_and_unpublication_uses_same_guard(self):
        token = self.confirmation()
        publish_questions(Question.objects.filter(pk=self.question.pk), self.editor)
        self.assertEqual(self.confirm(token).status_code, 409)
        withdrawal = self.confirmation("unpublish_selected")
        self.question.refresh_from_db()
        self.question.tag = "确认后改动"
        self.question.save()
        self.assertEqual(self.confirm(withdrawal, "unpublish_selected").status_code, 409)
        self.question.refresh_from_db()
        self.assertTrue(self.question.is_published)
        fresh = self.confirmation("unpublish_selected")
        self.assertEqual(self.confirm(fresh, "unpublish_selected").status_code, 302)
        self.question.refresh_from_db()
        self.assertFalse(self.question.is_published)

    def test_service_verifies_token_with_locked_current_rows(self):
        token = make_publication_token([self.question, self.other], self.editor, "publish_selected")
        self.question.tag = "变更"
        self.question.save()
        with self.assertRaises(ContentConflict):
            publish_questions(Question.objects.filter(pk__in=[self.question.pk, self.other.pk]), self.editor, confirmation_token=token)
        self.assertEqual(QuestionRevision.objects.count(), 0)
        draft_token = make_draft_token(self.question, self.editor)
        stale = Question.objects.get(pk=self.question.pk)
        self.question.answers = ["更新"]
        self.question.save()
        stale.answers = ["陈旧"]
        with self.assertRaises(ContentConflict):
            save_question_draft(stale, self.editor, draft_token)
        self.question.refresh_from_db()
        self.assertEqual(self.question.answers, ["更新"])

    def test_withdrawal_rejects_entire_batch_when_one_question_changed(self):
        ids = [self.question.pk, self.other.pk]
        publish_questions(Question.objects.filter(pk__in=ids), self.editor)
        before_logs = LogEntry.objects.count()
        token = self.confirmation("unpublish_selected", ids=ids)
        self.other.refresh_from_db()
        self.other.tag = "并发草稿"
        self.other.save()
        self.assertEqual(self.confirm(token, "unpublish_selected", ids=ids).status_code, 409)
        self.assertEqual(Question.objects.filter(is_published=True).count(), 2)
        self.assertEqual(QuestionRevision.objects.count(), 2)
        self.assertEqual(LogEntry.objects.count(), before_logs)

    def test_confirmation_rejects_changed_then_restored_content_and_reduced_set(self):
        ids = [self.question.pk, self.other.pk]
        token = self.confirmation(ids=ids)
        self.assertEqual(self.confirm(token, ids=[self.question.pk]).status_code, 409)
        self.question.tag = "临时修改"
        self.question.save()
        self.question.tag = ""
        self.question.save()
        self.assertEqual(self.confirm(token, ids=ids).status_code, 409)
        self.assertEqual(QuestionRevision.objects.count(), 0)

    def test_withdrawal_of_invalid_draft_is_still_supported(self):
        publish_questions(Question.objects.filter(pk=self.question.pk), self.editor)
        Question.objects.filter(pk=self.question.pk).update(answers=[])
        token = self.confirmation("unpublish_selected")
        self.assertEqual(self.confirm(token, "unpublish_selected").status_code, 302)
        self.question.refresh_from_db()
        self.assertFalse(self.question.is_published)
        self.assertEqual(QuestionRevision.objects.count(), 1)

    def test_draft_save_permission_and_csrf_cannot_be_bypassed_with_valid_token(self):
        token = self.get_edit_token()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.editor)
        response = client.post(self.change_url, {"draft_token": token, "_save": "保存"})
        self.assertEqual(response.status_code, 403)
        self.editor.user_permissions.clear()
        fresh_actor = get_user_model().objects.get(pk=self.editor.pk)
        with self.assertRaises(PermissionDenied):
            save_question_draft(self.question, fresh_actor, token)
        self.question.refresh_from_db()
        self.assertEqual(self.question.answers, ["原值"])
