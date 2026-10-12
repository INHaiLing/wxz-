"""Draft and immutable published content for the applied-writing catalogue."""

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class RetainedQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("应用文记录不能物理删除，请使用停用或下架操作。")


class ImmutableRevisionQuerySet(RetainedQuerySet):
    def update(self, **kwargs):
        raise ValidationError("已发布应用文修订不可修改，请编辑草稿后重新发布。")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("已发布应用文修订不可修改。")

    def bulk_create(self, objs, **kwargs):
        if kwargs.get("update_conflicts"):
            raise ValidationError("不能通过批量操作覆盖历史应用文修订。")
        for obj in objs:
            obj.full_clean()
        return super().bulk_create(objs, **kwargs)


class StableIdentity(models.Model):
    """Stable public IDs prevent links from changing after an edit."""

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
            raise ValidationError({"id": "已保存的编号不能修改。"})

    def save(self, *args, **kwargs):
        self.full_clean()
        result = super().save(*args, **kwargs)
        self._loaded_id = self.pk
        return result

    def delete(self, *args, **kwargs):
        raise ValidationError("应用文记录不能物理删除，请使用停用或下架操作。")


class WritingCategory(StableIdentity):
    id = models.CharField("分类编号", primary_key=True, max_length=64)
    name = models.CharField("分类名称", max_length=120)
    sort_order = models.PositiveIntegerField("排序", default=0)
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        verbose_name = "应用文分类"
        verbose_name_plural = verbose_name
        ordering = ("sort_order", "id")

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not self.is_active and self.pk and WritingContent.objects.filter(
            is_published=True, published_revision__category_id=self.pk
        ).exists():
            raise ValidationError({"is_active": "该分类仍被已发布应用文引用，请先下架相关内容。"})


class WritingGenre(StableIdentity):
    id = models.CharField("文种编号", primary_key=True, max_length=64)
    name = models.CharField("文种名称", max_length=120)
    sort_order = models.PositiveIntegerField("排序", default=0)
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        verbose_name = "应用文文种"
        verbose_name_plural = verbose_name
        ordering = ("sort_order", "id")

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not self.is_active and self.pk and WritingContent.objects.filter(
            is_published=True, published_revision__genre_id=self.pk
        ).exists():
            raise ValidationError({"is_active": "该文种仍被已发布应用文引用，请先下架相关内容。"})


class WritingKind(models.TextChoices):
    TEMPLATE = "template", "写作模板"
    EXAMPLE = "example", "范文示例"
    PAST_EXAM = "past_exam", "历年真题"
    EXERCISE = "exercise", "专项练习"


class WritingContentFields(models.Model):
    kind = models.CharField("内容类型", max_length=20, choices=WritingKind.choices)
    title = models.CharField("标题", max_length=200)
    body = models.TextField("正文", help_text="纯文本内容，支持换行。专项练习这里填写练习题目。")
    reference_answer = models.TextField(
        "参考答案", blank=True,
        help_text="仅专项练习使用；第一版只展示题目和参考答案，不保存学员作答。",
    )
    sort_order = models.PositiveIntegerField("排序", default=0)

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        errors = {}
        if not isinstance(self.title, str) or not self.title.strip():
            errors["title"] = "标题不能为空。"
        if not isinstance(self.body, str) or not self.body.strip():
            errors["body"] = "正文不能为空。"
        if self.kind == WritingKind.EXERCISE:
            if not isinstance(self.reference_answer, str) or not self.reference_answer.strip():
                errors["reference_answer"] = "专项练习必须填写参考答案。"
        elif self.reference_answer:
            errors["reference_answer"] = "只有专项练习可以填写参考答案。"
        if errors:
            raise ValidationError(errors)

    def content_payload(self):
        return {
            "category_id": self.category_id,
            "genre_id": self.genre_id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "reference_answer": self.reference_answer,
            "sort_order": self.sort_order,
        }


class WritingContent(WritingContentFields, StableIdentity):
    id = models.CharField("内容编号", primary_key=True, max_length=64)
    category = models.ForeignKey(
        WritingCategory, verbose_name="应用文分类", on_delete=models.PROTECT,
        related_name="draft_contents",
    )
    genre = models.ForeignKey(
        WritingGenre, verbose_name="应用文文种", on_delete=models.PROTECT,
        related_name="draft_contents", null=True, blank=True,
    )
    published_revision = models.ForeignKey(
        "WritingRevision", verbose_name="当前发布修订", on_delete=models.PROTECT,
        null=True, blank=True, related_name="+",
    )
    is_published = models.BooleanField("已发布", default=False)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("草稿更新时间", auto_now=True)

    class Meta:
        verbose_name = "应用文内容"
        verbose_name_plural = verbose_name
        ordering = ("sort_order", "id")
        permissions = [
            ("publish_writingcontent", "可以发布及下架应用文"),
        ]

    def __str__(self):
        return f"{self.id} · {self.title}"

    def clean(self):
        super().clean()
        errors = {}
        if not self.category_id:
            errors["category"] = "应用文必须选择分类。"
        if self.is_published and not self.published_revision_id:
            errors["is_published"] = "发布状态必须关联有效的发布修订。"
        if self.published_revision_id and not WritingRevision.objects.filter(
            pk=self.published_revision_id, content_id=self.pk
        ).exists():
            errors["published_revision"] = "不能引用其他内容的发布修订。"
        if errors:
            raise ValidationError(errors)

    @property
    def has_unpublished_changes(self):
        return not self.published_revision_id or self.content_payload() != self.published_revision.content_payload()


class WritingRevision(WritingContentFields):
    content = models.ForeignKey(
        WritingContent, verbose_name="应用文内容", on_delete=models.PROTECT, related_name="revisions"
    )
    version = models.PositiveIntegerField("修订号", validators=[MinValueValidator(1)])
    category = models.ForeignKey(
        WritingCategory, verbose_name="应用文分类", on_delete=models.PROTECT,
        related_name="writing_revisions",
    )
    genre = models.ForeignKey(
        WritingGenre, verbose_name="应用文文种", on_delete=models.PROTECT,
        related_name="writing_revisions", null=True, blank=True,
    )
    created_at = models.DateTimeField("发布时间", auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="发布人", on_delete=models.PROTECT,
        null=True, blank=True, related_name="published_writing_revisions",
    )

    objects = ImmutableRevisionQuerySet.as_manager()

    class Meta:
        verbose_name = "应用文发布修订（只读）"
        verbose_name_plural = verbose_name
        ordering = ("content_id", "-version")
        constraints = [
            models.UniqueConstraint(fields=("content", "version"), name="writing_unique_content_revision"),
        ]

    def __str__(self):
        return f"{self.content_id} · v{self.version}"

    def save(self, *args, **kwargs):
        if not self._state.adding or (self.pk and type(self).objects.filter(pk=self.pk).exists()):
            raise ValidationError("已发布应用文修订不可修改，请编辑草稿后重新发布。")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("历史应用文发布修订不能物理删除。")
