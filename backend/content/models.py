"""Question drafts and immutable published content for the local admin backend."""

import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, router, transaction


def new_question_id():
    return f"q-{uuid.uuid4().hex}"


class RetainedQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("内容记录不能物理删除，请使用停用或下架操作。")


class ImmutableRevisionQuerySet(RetainedQuerySet):
    def update(self, **kwargs):
        raise ValidationError("已发布修订不可修改，请修改题目草稿后重新发布。")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("已发布修订不可修改。")

    def bulk_create(self, objs, **kwargs):
        if kwargs.get("update_conflicts"):
            raise ValidationError("不能通过批量操作覆盖历史修订。")
        for obj in objs:
            obj.full_clean()
        return super().bulk_create(objs, **kwargs)


class StableIdentity(models.Model):
    """Business IDs are editable only before the first successful save."""

    objects = RetainedQuerySet.as_manager()

    class Meta:
        abstract = True

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_id = instance.pk
        return instance

    def clean(self):
        super().clean()
        if not self.pk or not str(self.pk).strip():
            raise ValidationError({"id": "编号不能为空。"})
        if not re.fullmatch(r"[-A-Za-z0-9_]+", str(self.pk)):
            raise ValidationError({"id": "编号只能使用英文字母、数字、下划线和连字符。"})
        if not self._state.adding and getattr(self, "_loaded_id", self.pk) != self.pk:
            raise ValidationError({"id": "已保存的编号不能修改，请创建新的记录。"})

    def save(self, *args, **kwargs):
        self.full_clean()
        result = super().save(*args, **kwargs)
        self._loaded_id = self.pk
        return result

    def delete(self, *args, **kwargs):
        raise ValidationError("内容记录不能物理删除，请使用停用或下架操作。")


class Category(StableIdentity):
    id = models.CharField("分类编号", primary_key=True, max_length=64)
    name = models.CharField("分类名称", max_length=120)
    sort_order = models.PositiveIntegerField("排序", default=0)
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        verbose_name = "文学分类"
        verbose_name_plural = verbose_name
        ordering = ("sort_order", "id")

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not self.is_active and self.pk and Question.objects.filter(
            is_published=True, published_revision__category_id=self.pk
        ).exists():
            raise ValidationError({"is_active": "该分类仍被已发布题目引用，请先下架相关题目。"})

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            if not self._state.adding:
                type(self).objects.using(using).select_for_update().filter(pk=self.pk).first()
            return super().save(*args, **kwargs)


class Article(StableIdentity):
    id = models.CharField("篇目编号", primary_key=True, max_length=64)
    title = models.CharField("篇目名称", max_length=160)
    sort_order = models.PositiveIntegerField("排序", default=0)
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        verbose_name = "文言篇目"
        verbose_name_plural = verbose_name
        ordering = ("sort_order", "id")

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if not self.is_active and self.pk and Question.objects.filter(
            is_published=True, published_revision__article_id=self.pk
        ).exists():
            raise ValidationError({"is_active": "该篇目仍被已发布题目引用，请先下架相关题目。"})

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            if not self._state.adding:
                type(self).objects.using(using).select_for_update().filter(pk=self.pk).first()
            return super().save(*args, **kwargs)


class Source(models.TextChoices):
    LITERATURE = "literature", "文学常识"
    CLASSICAL = "classical", "文言文"


class QuestionType(models.TextChoices):
    FACT = "fact", "常识"
    WORD = "word", "字词"
    TRANSLATION = "translation", "翻译"


class QuestionContent(models.Model):
    source = models.CharField("来源", max_length=20, choices=Source.choices)
    type = models.CharField("题型", max_length=20, choices=QuestionType.choices)
    tag = models.CharField("标签", max_length=80, blank=True)
    stem = models.TextField("题干", help_text="用 {{0}}、{{1}} 等表示答案位置，可重复引用相同位置。")
    answers = models.JSONField("答案", default=list)
    sort_order = models.PositiveIntegerField("排序", default=0)

    class Meta:
        abstract = True

    def content_payload(self):
        return {
            "source": self.source,
            "category_id": self.category_id,
            "article_id": self.article_id,
            "type": self.type,
            "tag": self.tag,
            "stem": self.stem,
            "answers": self.answers,
            "sort_order": self.sort_order,
        }

    def clean(self):
        super().clean()
        errors = {}
        if self.source == Source.LITERATURE:
            if not self.category_id:
                errors["category"] = "文学常识题必须选择文学分类。"
            if self.article_id:
                errors["article"] = "文学常识题不能关联文言篇目。"
            if self.type != QuestionType.FACT:
                errors["type"] = "文学常识只支持常识题型。"
        elif self.source == Source.CLASSICAL:
            if not self.article_id:
                errors["article"] = "文言题必须选择篇目。"
            if self.category_id:
                errors["category"] = "文言题不能关联文学分类。"

        valid_answers = (
            isinstance(self.answers, list)
            and bool(self.answers)
            and all(isinstance(answer, str) and answer.strip() for answer in self.answers)
        )
        if not valid_answers:
            errors["answers"] = "答案必须是非空字符串数组，每个答案都不能为空。"

        stem = self.stem if isinstance(self.stem, str) else ""
        if not stem.strip():
            errors["stem"] = "题干不能为空。"
        else:
            tokens = re.findall(r"\{\{([0-9]+)\}\}", stem)
            remainder = re.sub(r"\{\{[0-9]+\}\}", "", stem)
            if "{{" in remainder or "}}" in remainder:
                errors["stem"] = "答案占位符格式不正确，请使用 {{0}}、{{1}} 等连续编号。"
            # Compare canonical digit strings rather than parsing unbounded integers.
            # A malformed {{999...}} token must produce a field error, never a 500.
            elif valid_answers and {token.lstrip("0") or "0" for token in tokens} != {
                str(index) for index in range(len(self.answers))
            }:
                errors["stem"] = "占位符编号必须从 0 连续到最后一个答案，且每个答案至少出现一次。"
        if errors:
            raise ValidationError(errors)


class Question(QuestionContent, StableIdentity):
    id = models.CharField("题目编号", primary_key=True, max_length=64, default=new_question_id)
    category = models.ForeignKey(
        Category, verbose_name="文学分类", on_delete=models.PROTECT,
        null=True, blank=True, related_name="draft_questions",
    )
    article = models.ForeignKey(
        Article, verbose_name="文言篇目", on_delete=models.PROTECT,
        null=True, blank=True, related_name="draft_questions",
    )
    published_revision = models.ForeignKey(
        "QuestionRevision", verbose_name="当前发布修订", on_delete=models.PROTECT,
        null=True, blank=True, related_name="+",
    )
    is_published = models.BooleanField("已发布", default=False)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("草稿/发布更新时间", auto_now=True)

    class Meta:
        verbose_name = "题目草稿"
        verbose_name_plural = verbose_name
        ordering = ("sort_order", "id")
        permissions = [
            ("publish_question", "可以发布及下架题目"),
            ("import_question", "可以导入题目草稿"),
        ]

    def __str__(self):
        return f"{self.id} · {self.stem[:48]}"

    def clean(self):
        super().clean()
        if self.is_published and not self.published_revision_id:
            raise ValidationError({"is_published": "发布状态必须关联有效的发布修订。"})
        if self.published_revision_id:
            belongs_to_question = QuestionRevision.objects.filter(
                pk=self.published_revision_id, question_id=self.pk
            ).exists()
            if not belongs_to_question:
                raise ValidationError({"published_revision": "不能引用其他题目的发布修订。"})

    @property
    def has_unpublished_changes(self):
        return not self.published_revision_id or self.content_payload() != self.published_revision.content_payload()


class QuestionRevision(QuestionContent):
    question = models.ForeignKey(
        Question, verbose_name="题目", on_delete=models.PROTECT, related_name="revisions"
    )
    version = models.PositiveIntegerField("修订号", validators=[MinValueValidator(1)])
    category = models.ForeignKey(
        Category, verbose_name="文学分类", on_delete=models.PROTECT,
        null=True, blank=True, related_name="question_revisions",
    )
    article = models.ForeignKey(
        Article, verbose_name="文言篇目", on_delete=models.PROTECT,
        null=True, blank=True, related_name="question_revisions",
    )
    created_at = models.DateTimeField("发布时间", auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="发布人", on_delete=models.PROTECT,
        null=True, blank=True, related_name="published_question_revisions",
    )

    objects = ImmutableRevisionQuerySet.as_manager()

    class Meta:
        verbose_name = "发布修订（只读）"
        verbose_name_plural = verbose_name
        ordering = ("question_id", "-version")
        constraints = [
            models.UniqueConstraint(fields=("question", "version"), name="content_unique_question_revision"),
        ]

    def __str__(self):
        return f"{self.question_id} · v{self.version}"

    def save(self, *args, **kwargs):
        if not self._state.adding or (self.pk and type(self).objects.filter(pk=self.pk).exists()):
            raise ValidationError("已发布修订不可修改，请编辑草稿后重新发布。")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("历史发布修订不能物理删除。")
