from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from content.access import accessible_questions, can_access_question, free_question_ids, published_questions
from content.models import Article, Category, LearningConfiguration, Question, QuestionRevision


class StudentContentFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(id="quanxue", title="劝学")
        cls.other_article = Article.objects.create(id="shishuo", title="师说")
        cls.category = Category.objects.create(id="pre-qin", name="先秦文学")
        LearningConfiguration.objects.create(page_size=2)
        cls.first = cls.make_question("01-word", type="word", order=1)
        cls.second = cls.make_question("02-fact", type="fact", order=2)
        cls.paid = cls.make_question("03-translation", type="translation", order=3)
        cls.other = cls.make_question("other-translation", type="translation", order=1, article=cls.other_article)
        cls.literature = cls.make_question("literature", source="literature", type="fact", order=1)
        cls.draft = cls.make_question("draft", type="translation", order=0, published=False)

    @classmethod
    def make_question(cls, pk, *, source="classical", type="word", order=0, article=None, published=True):
        values = {
            "source": source, "type": type, "stem": f"{pk}内容{{{{0}}}}。", "answers": [f"{pk}答案"],
            "article": (article or cls.article) if source == "classical" else None,
            "category": cls.category if source == "literature" else None, "sort_order": order,
        }
        question = Question.objects.create(id=pk, **values)
        if published:
            cls.publish(question)
        return question

    @staticmethod
    def publish(question):
        version = question.revisions.count() + 1
        revision = QuestionRevision.objects.create(question=question, version=version, **question.content_payload())
        question.published_revision = revision
        question.is_published = True
        question.save()


class PublishedAccessTests(StudentContentFixture):
    def test_free_ids_are_shared_across_all_article_types(self):
        self.assertEqual(free_question_ids("classical", self.article.pk), [self.first.pk, self.second.pk])
        translations = published_questions().filter(published_revision__type="translation")
        self.assertEqual(list(accessible_questions(None, translations).values_list("pk", flat=True)), [self.other.pk])
        self.assertFalse(can_access_question(None, self.paid))
        self.assertTrue(can_access_question(None, self.other))
        self.assertTrue(can_access_question(None, self.literature))

    def test_literature_scope_has_its_own_limit_and_tied_orders_use_stable_ids(self):
        second = self.make_question("literature-second", source="literature", type="fact", order=2)
        paid = self.make_question("literature-third", source="literature", type="fact", order=3)
        self.assertEqual(free_question_ids("literature", self.category.pk), [self.literature.pk, second.pk])
        self.assertFalse(can_access_question(None, paid))
        tied = self.make_question("00-tied", order=1)
        self.assertEqual(free_question_ids("classical", self.article.pk), [tied.pk, self.first.pk])

    def test_scope_is_based_on_snapshot_not_edited_draft(self):
        self.first.article = self.other_article
        self.first.sort_order = 100
        self.first.answers = ["未发布草稿答案"]
        self.first.save()
        self.assertEqual(free_question_ids("classical", self.article.pk), [self.first.pk, self.second.pk])
        current = published_questions().get(pk=self.first.pk)
        self.assertNotEqual(current.published_revision.answers, self.first.answers)

    def test_later_publication_withdrawal_and_capacity_change_recalculate_free_set(self):
        self.first.sort_order = 10
        self.publish(self.first)
        self.assertEqual(free_question_ids("classical", self.article.pk), [self.second.pk, self.paid.pk])
        self.assertTrue(can_access_question(None, self.paid))
        Question.objects.filter(pk=self.second.pk).update(is_published=False)
        self.assertEqual(free_question_ids("classical", self.article.pk), [self.paid.pk, self.first.pk])
        config = LearningConfiguration.current()
        config.page_size = 1
        config.save()
        self.assertFalse(can_access_question(None, self.first))

    def test_draft_withdrawn_inactive_and_wrong_revision_are_excluded(self):
        self.assertFalse(can_access_question(None, self.draft))
        Article.objects.filter(pk=self.article.pk).update(is_active=False)
        self.assertFalse(can_access_question(None, self.first))
        Article.objects.filter(pk=self.article.pk).update(is_active=True)
        Question.objects.filter(pk=self.first.pk).update(published_revision=self.other.published_revision)
        self.assertFalse(can_access_question(None, self.first))
        Question.objects.filter(pk=self.other.pk).update(is_published=False)
        self.assertFalse(can_access_question(None, self.other))

    def test_type_limit_is_inside_related_subquery_not_outer_prefilter(self):
        queryset = published_questions().filter(published_revision__article_id=self.article.pk, published_revision__type="translation")
        with self.assertNumQueries(1):
            self.assertEqual(list(accessible_questions(None, queryset, page_size=2)), [])


class LearningConfigurationTests(TestCase):
    def test_current_returns_unsaved_defaults_without_creating_row(self):
        config = LearningConfiguration.current()
        self.assertTrue(config._state.adding)
        self.assertEqual((config.page_size, config.daily_target, config.exam_date), (20, 20, None))
        self.assertFalse(LearningConfiguration.objects.exists())

    def test_saved_settings_preserve_date_and_validate_boundaries(self):
        config = LearningConfiguration.objects.create(page_size=1, exam_date=date(2027, 3, 1))
        self.assertEqual(LearningConfiguration.current().exam_date, date(2027, 3, 1))
        config.page_size = 100
        config.save()
        for value in (0, 101):
            config.page_size = value
            with self.assertRaises(ValidationError):
                config.save()
        with self.assertRaises(ValidationError):
            LearningConfiguration(id=2).save()

    def test_database_constraints_block_bulk_bypass(self):
        LearningConfiguration.objects.create()
        for change in ({"page_size": 0}, {"page_size": 101}, {"daily_target": 0}):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    LearningConfiguration.objects.update(**change)
