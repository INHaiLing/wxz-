"""Real row-lock races: skipped on SQLite, exercised by PostgreSQL CI."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connections, transaction
from django.test import Client, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from content.models import Category, Question, QuestionRevision
from content.services import (
    ContentConflict, make_draft_token, make_publication_token,
    publish_questions, save_question_draft,
)


@skipUnlessDBFeature("has_select_for_update")
class ContentConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.category = Category.objects.create(id="race", name="事务竞争")
        self.question = Question.objects.create(
            id="race-q", source="literature", type="fact", category=self.category,
            stem="答案{{0}}", answers=["原值"],
        )
        self.actor = get_user_model().objects.create_user(username="race-editor", is_staff=True)
        self.actor.user_permissions.set(Permission.objects.filter(
            content_type__app_label="content",
            codename__in=("view_question", "change_question", "publish_question"),
        ))

    def run_separate_connection(self, operation):
        connections.close_all()
        try:
            return operation()
        finally:
            connections.close_all()

    def test_two_http_saves_validate_together_but_only_one_commits(self):
        url = reverse("admin:content_question_change", args=(self.question.pk,))
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(self.actor)
        token = clients[0].get(url).context["adminform"].form["draft_token"].value()
        barrier = Barrier(2)

        def synchronized_save(*args, **kwargs):
            barrier.wait(timeout=15)
            return save_question_draft(*args, **kwargs)

        def submit(index):
            return clients[index].post(url, {
                "source": "literature", "category": self.category.pk, "article": "",
                "type": "fact", "tag": "", "sort_order": "0", "stem": "答案{{0}}",
                "answers": f"设备{index}", "draft_token": token, "_save": "保存",
            }).status_code

        with patch("content.admin.save_question_draft", side_effect=synchronized_save):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.run_separate_connection, lambda i=i: submit(i)) for i in range(2)]
                statuses = [future.result(timeout=30) for future in futures]
        self.assertEqual(sorted(statuses), [302, 409])
        self.question.refresh_from_db()
        self.assertIn(self.question.answers, (["设备0"], ["设备1"]))
        self.assertFalse(self.question.is_published)

    def test_publication_waits_for_editor_commit_then_rejects_old_confirmation(self):
        token = make_publication_token([self.question], self.actor, "publish_selected")
        edit_token = make_draft_token(self.question, self.actor)
        locked = Event()
        publishing = Event()

        def edit():
            actor = get_user_model().objects.get(pk=self.actor.pk)
            with transaction.atomic():
                question = Question.objects.select_for_update().get(pk=self.question.pk)
                question.answers = ["并发新草稿"]
                save_question_draft(question, actor, edit_token)
                locked.set()
                if not publishing.wait(timeout=15):
                    raise AssertionError("发布线程未开始")

        def publish():
            if not locked.wait(timeout=15):
                raise AssertionError("编辑线程未加锁")
            actor = get_user_model().objects.get(pk=self.actor.pk)
            publishing.set()
            try:
                publish_questions(Question.objects.filter(pk=self.question.pk), actor, confirmation_token=token)
            except ContentConflict:
                return "conflict"
            return "published"

        with ThreadPoolExecutor(max_workers=2) as pool:
            editor = pool.submit(self.run_separate_connection, edit)
            publisher = pool.submit(self.run_separate_connection, publish)
            editor.result(timeout=30)
            self.assertEqual(publisher.result(timeout=30), "conflict")
        self.question.refresh_from_db()
        self.assertEqual(self.question.answers, ["并发新草稿"])
        self.assertFalse(self.question.is_published)
        self.assertEqual(QuestionRevision.objects.count(), 0)
