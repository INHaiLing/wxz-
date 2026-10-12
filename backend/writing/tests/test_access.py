from django.core.exceptions import ValidationError
from django.test import TestCase

from content.models import LearningConfiguration

from writing.access import accessible_writings, free_writings, published_writings
from writing.models import WritingCategory, WritingContent, WritingGenre, WritingRevision


class WritingFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = WritingCategory.objects.create(id="notice", name="通知类", sort_order=1)
        cls.other_category = WritingCategory.objects.create(id="report", name="报告类", sort_order=2)
        cls.genre = WritingGenre.objects.create(id="notice-genre", name="通知", sort_order=1)
        LearningConfiguration.objects.create(page_size=2)
        cls.first = cls.make_content("w-01", kind="template", order=1)
        cls.second = cls.make_content("w-02", kind="example", order=2)
        cls.paid = cls.make_content("w-03", kind="exercise", order=3, reference_answer="参考答案")
        cls.other = cls.make_content("w-04", category=cls.other_category, kind="past_exam", order=1)
        cls.draft = cls.make_content("w-draft", kind="example", order=0, published=False)

    @classmethod
    def make_content(cls, pk, *, category=None, kind="template", order=0, reference_answer="", published=True):
        content = WritingContent.objects.create(
            id=pk, category=category or cls.category, genre=cls.genre,
            kind=kind, title=f"{pk}标题", body=f"{pk}正文\n第二行", reference_answer=reference_answer,
            sort_order=order,
        )
        if published:
            revision = WritingRevision.objects.create(
                content=content, version=1, category=content.category, genre=content.genre,
                kind=content.kind, title=content.title, body=content.body,
                reference_answer=content.reference_answer, sort_order=content.sort_order,
            )
            content.published_revision = revision
            content.is_published = True
            content.save()
        return content


class WritingAccessTests(WritingFixture):
    def test_first_page_is_shared_by_all_kinds_in_one_category(self):
        self.assertEqual(list(free_writings(published_writings().filter(published_revision__category=self.category)).values_list("pk", flat=True)), ["w-01", "w-02"])
        self.assertEqual(list(free_writings(published_writings().filter(published_revision__kind="exercise")).values_list("pk", flat=True)), [])

    def test_other_category_gets_its_own_free_page(self):
        self.assertTrue(free_writings(published_writings().filter(pk=self.other.pk)).exists())

    def test_draft_and_inactive_scopes_are_not_accessible(self):
        self.assertFalse(published_writings().filter(pk=self.draft.pk).exists())
        WritingCategory.objects.filter(pk=self.category.pk).update(is_active=False)
        self.assertFalse(published_writings().filter(pk=self.first.pk).exists())

    def test_revision_is_immutable(self):
        with self.assertRaises(ValidationError):
            self.first.published_revision.body = "改写历史"
            self.first.published_revision.save()
        with self.assertRaises(ValidationError):
            self.first.published_revision.delete()
