from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from content.models import Article, Category, Question, QuestionRevision
from content.services import publish_questions, unpublish_questions


class PublicationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(id="pre-qin", name="先秦文学")
        cls.article = Article.objects.create(id="quanxue", title="劝学")
        cls.publisher = get_user_model().objects.create_user(username="publisher", is_staff=True)
        cls.publisher.user_permissions.add(
            Permission.objects.get(content_type__app_label="content", codename="publish_question")
        )
        cls.editor = get_user_model().objects.create_user(username="editor", is_staff=True)
        cls.editor.user_permissions.add(
            Permission.objects.get(content_type__app_label="content", codename="change_question")
        )

    def make_question(self, question_id="lit-count", **changes):
        values = {
            "id": question_id, "source": "literature", "type": "fact", "category": self.category,
            "stem": "《诗经》收录{{0}}篇。", "answers": ["305"], "tag": "文学常识",
        }
        values.update(changes)
        return Question.objects.create(**values)

    def selected(self, *questions):
        return Question.objects.filter(pk__in=[question.pk for question in questions])

    def test_publication_keeps_draft_edits_separate_from_published_revision(self):
        question = self.make_question()
        self.assertEqual(publish_questions(self.selected(question), self.publisher), 1)
        question.refresh_from_db()
        original_revision = question.published_revision
        self.assertEqual(original_revision.stem, "《诗经》收录{{0}}篇。")

        question.stem = "《诗经》又称{{0}}。"
        question.answers = ["诗三百"]
        question.save()
        self.assertTrue(question.has_unpublished_changes)
        original_revision.refresh_from_db()
        self.assertEqual(original_revision.answers, ["305"])
        self.assertEqual(question.published_revision_id, original_revision.pk)

        self.assertEqual(publish_questions(self.selected(question), self.publisher), 1)
        question.refresh_from_db()
        self.assertEqual(question.published_revision.version, 2)
        self.assertEqual(question.published_revision.answers, ["诗三百"])
        self.assertEqual(question.revisions.count(), 2)
        self.assertFalse(question.has_unpublished_changes)

    def test_repeat_publication_and_unchanged_republication_reuse_revision(self):
        question = self.make_question()
        self.assertEqual(publish_questions(self.selected(question), self.publisher), 1)
        self.assertEqual(publish_questions(self.selected(question), self.publisher), 0)
        self.assertEqual(unpublish_questions(self.selected(question), self.publisher), 1)
        self.assertEqual(unpublish_questions(self.selected(question), self.publisher), 0)
        question.refresh_from_db()
        self.assertFalse(question.is_published)
        saved_revision = question.published_revision_id
        self.assertEqual(publish_questions(self.selected(question), self.publisher), 1)
        question.refresh_from_db()
        self.assertTrue(question.is_published)
        self.assertEqual(question.published_revision_id, saved_revision)
        self.assertEqual(question.revisions.count(), 1)
        self.assertEqual(LogEntry.objects.filter(object_id=question.pk, action_flag=CHANGE).count(), 3)

    def test_admin_related_querysets_lock_only_questions_and_preserve_revision(self):
        question = self.make_question()
        related_queryset = self.selected(question).select_related("category", "article", "published_revision")
        revision_id = None
        for service, expected_count, expected_published in (
            (publish_questions, 1, True),
            (publish_questions, 0, True),
            (unpublish_questions, 1, False),
            (publish_questions, 1, True),
        ):
            with self.subTest(service=service.__name__, count=expected_count):
                with CaptureQueriesContext(connection) as queries:
                    self.assertEqual(service(related_queryset, self.publisher), expected_count)
                question_fetch = next(
                    query["sql"] for query in queries.captured_queries
                    if query["sql"].lstrip().upper().startswith("SELECT")
                    and 'FROM "content_question"' in query["sql"]
                )
                self.assertNotIn(" JOIN ", question_fetch.upper())
                question.refresh_from_db()
                revision_id = revision_id or question.published_revision_id
                self.assertEqual(question.published_revision_id, revision_id)
                self.assertEqual(question.is_published, expected_published)
                self.assertEqual(question.published_revision.answers, ["305"])
                self.assertEqual(question.revisions.count(), 1)

    def test_service_checks_staff_activity_and_publication_permission(self):
        question = self.make_question()
        nonstaff = get_user_model().objects.create_user(username="not-staff")
        nonstaff.user_permissions.set(self.publisher.user_permissions.all())
        inactive = get_user_model().objects.create_user(username="inactive", is_staff=True, is_active=False)
        inactive.user_permissions.set(self.publisher.user_permissions.all())
        for actor in (self.editor, nonstaff, inactive, None):
            for service in (publish_questions, unpublish_questions):
                with self.subTest(actor=getattr(actor, "username", None), service=service.__name__):
                    with self.assertRaises(PermissionDenied):
                        service(self.selected(question), actor)
        question.refresh_from_db()
        self.assertFalse(question.is_published)
        self.assertEqual(QuestionRevision.objects.count(), 0)
        self.assertEqual(LogEntry.objects.count(), 0)

    def test_invalid_draft_prevents_the_entire_batch_from_publishing(self):
        good = self.make_question("a-good")
        invalid = self.make_question("z-invalid")
        Question.objects.filter(pk=invalid.pk).update(answers=[])
        with self.assertRaises(ValidationError):
            publish_questions(self.selected(good, invalid), self.publisher)
        self.assertEqual(Question.objects.filter(is_published=True).count(), 0)
        self.assertEqual(QuestionRevision.objects.count(), 0)

        self.assertEqual(LogEntry.objects.count(), 0)

    def test_oversized_placeholder_is_a_validation_error(self):
        with self.assertRaises(ValidationError) as raised:
            self.make_question(stem="错误占位符{{" + "9" * 5000 + "}}")
        self.assertIn("stem", raised.exception.message_dict)
        self.assertEqual(Question.objects.count(), 0)
        self.assertEqual(LogEntry.objects.count(), 0)

    def test_disabled_relation_is_allowed_in_draft_but_not_publication(self):
        self.category.is_active = False
        self.category.save()
        question = self.make_question()
        with self.assertRaises(ValidationError):
            publish_questions(self.selected(question), self.publisher)
        self.assertEqual(QuestionRevision.objects.count(), 0)

    def test_withdrawal_is_possible_even_when_draft_is_invalid(self):
        question = self.make_question()
        publish_questions(self.selected(question), self.publisher)
        question.refresh_from_db()
        revision_id = question.published_revision_id
        Question.objects.filter(pk=question.pk).update(answers=[])
        self.assertEqual(unpublish_questions(self.selected(question), self.publisher), 1)
        question.refresh_from_db()
        self.assertFalse(question.is_published)
        self.assertEqual(question.published_revision_id, revision_id)
        self.assertEqual(question.revisions.count(), 1)

    def test_catalog_deactivation_uses_published_not_draft_associations(self):
        other = Category.objects.create(id="tang", name="唐代文学")
        question = self.make_question()
        publish_questions(self.selected(question), self.publisher)
        question.refresh_from_db()
        question.category = other
        question.save()
        self.category.is_active = False
        with self.assertRaises(ValidationError):
            self.category.save()
        other.is_active = False
        other.save()
        unpublish_questions(self.selected(question), self.publisher)
        self.category.save()

    def test_published_article_must_be_withdrawn_before_deactivation(self):
        question = self.make_question(
            source="classical", type="word", category=None, article=self.article,
            stem="“輮”的意思是{{0}}。", answers=["使木材弯曲"],
        )
        publish_questions(self.selected(question), self.publisher)
        self.article.is_active = False
        with self.assertRaises(ValidationError):
            self.article.save()
        unpublish_questions(self.selected(question), self.publisher)
        self.article.save()

    def test_revisions_cannot_be_changed_deleted_or_attached_to_another_question(self):
        question = self.make_question()
        other = self.make_question("other")
        publish_questions(self.selected(question), self.publisher)
        question.refresh_from_db()
        revision = question.published_revision
        revision.answers = ["篡改历史"]
        with self.assertRaises(ValidationError):
            revision.save()
        with self.assertRaises(ValidationError):
            QuestionRevision.objects.filter(pk=revision.pk).update(answers=["篡改历史"])
        with self.assertRaises(ValidationError):
            revision.delete()
        with self.assertRaises(ValidationError):
            QuestionRevision.objects.filter(pk=revision.pk).delete()
        other.published_revision = revision
        other.is_published = True
        with self.assertRaises(ValidationError):
            other.save()
        revision.refresh_from_db()
        self.assertEqual(revision.answers, ["305"])

    def test_business_ids_are_stable_and_content_cannot_be_deleted(self):
        question = self.make_question()
        for instance in (self.category, self.article, question):
            with self.subTest(model=type(instance).__name__):
                original_id = instance.pk
                instance.id = "changed-id"
                with self.assertRaises(ValidationError):
                    instance.save()
                instance.id = original_id
                with self.assertRaises(ValidationError):
                    instance.delete()
                with self.assertRaises(ValidationError):
                    type(instance).objects.filter(pk=original_id).delete()

    def test_validation_requires_complete_answer_indices_and_matching_associations(self):
        valid = self.make_question(stem="{{0}}也称{{0}}。")
        self.assertEqual(valid.answers, ["305"])
        bad_cases = [
            {"answers": []}, {"answers": "305"}, {"answers": [""]}, {"answers": [305]},
            {"answers": ["305", "诗三百"]}, {"stem": "{{1}}。"}, {"stem": "{{x}}。"},
            {"stem": "{{0}}和{{-1}}。"}, {"stem": "{{0}}和{{未结束"},
            {"source": "literature", "type": "word"}, {"category": None},
            {"article": self.article}, {"source": "classical", "article": None, "category": None},
            {"source": "classical", "article": self.article}, {"id": "中文编号"}, {"id": "has space"},
        ]
        for index, changes in enumerate(bad_cases):
            values = {
                "id": f"invalid-{index}", "source": "literature", "type": "fact",
                "category": self.category, "stem": "{{0}}。", "answers": ["305"],
            }
            values.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):
                    Question(**values).full_clean()
