import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


MODES = (("writing", "默写"), ("reciting", "背诵"))
TYPES = (("", "全部题型"), ("fact", "常识"), ("word", "字词"), ("translation", "翻译"))


class PracticeQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("练习记录只能通过服务修改。")

    def bulk_update(self, *args, **kwargs):
        raise ValidationError("练习记录不能批量覆盖。")

    def delete(self):
        raise ValidationError("练习历史不能删除。")

    def bulk_create(self, objs, *, _service=False, **kwargs):
        if not _service or kwargs.get("update_conflicts") or kwargs.get("ignore_conflicts"):
            raise ValidationError("练习记录只能通过服务新增。")
        for obj in objs:
            obj.clean()
        return super().bulk_create(objs, **kwargs)


class PracticeRecord(models.Model):
    objects = PracticeQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, _service=False, **kwargs):
        if not _service:
            raise ValidationError("练习记录只能通过服务写入。")
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("练习历史不能删除。")


def scope_condition():
    return (
        models.Q(source="literature", category__isnull=False, article__isnull=True, question_type__in=("", "fact"))
        | models.Q(source="classical", article__isnull=False, category__isnull=True, question_type__in=("", "fact", "word", "translation"))
    )


class ScopeRecord(PracticeRecord):
    source = models.CharField("来源", max_length=20, choices=(("literature", "文学常识"), ("classical", "文言文")))
    category = models.ForeignKey("content.Category", on_delete=models.PROTECT, null=True, blank=True, verbose_name="分类")
    article = models.ForeignKey("content.Article", on_delete=models.PROTECT, null=True, blank=True, verbose_name="篇目")
    question_type = models.CharField("题型", max_length=20, choices=TYPES, default="", blank=True)
    mode = models.CharField("模式", max_length=16, choices=MODES)

    class Meta:
        abstract = True


class PracticeRound(ScopeRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name="学员")
    group_size = models.PositiveSmallIntegerField("固定每组题数")
    item_count = models.PositiveIntegerField("固定题量")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "随机轮次"
        verbose_name_plural = verbose_name
        constraints = [
            models.CheckConstraint(condition=scope_condition(), name="practice_round_scope"),
            models.CheckConstraint(condition=models.Q(group_size__gte=1, group_size__lte=100), name="practice_round_group_size"),
            models.CheckConstraint(condition=models.Q(item_count__gte=1, item_count__lte=10000), name="practice_round_item_count"),
            models.CheckConstraint(condition=models.Q(mode__in=("writing", "reciting")), name="practice_round_mode"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("随机轮次不可修改。")
        return super().save(*args, **kwargs)

    @property
    def group_count(self):
        return (self.item_count + self.group_size - 1) // self.group_size

    def __str__(self):
        return f"轮次 {self.pk}"


class RoundItem(PracticeRecord):
    round = models.ForeignKey(PracticeRound, on_delete=models.PROTECT, related_name="items", verbose_name="轮次")
    position = models.PositiveIntegerField("固定序号")
    question = models.ForeignKey("content.Question", on_delete=models.PROTECT, verbose_name="题目")
    revision = models.ForeignKey("content.QuestionRevision", on_delete=models.PROTECT, verbose_name="固定修订")

    class Meta:
        verbose_name = "轮次题目"
        verbose_name_plural = verbose_name
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(fields=("round", "position"), name="practice_round_position_unique"),
            models.UniqueConstraint(fields=("round", "question"), name="practice_round_question_unique"),
            models.CheckConstraint(condition=models.Q(position__gte=1, position__lte=10000), name="practice_item_position_range"),
        ]

    def clean(self):
        super().clean()
        if self.revision_id and self.revision.question_id != self.question_id:
            raise ValidationError("固定修订必须属于相同题目。")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("轮次题目不可修改。")
        return super().save(*args, **kwargs)


class ResumePosition(ScopeRecord):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name="学员")
    question = models.ForeignKey("content.Question", on_delete=models.PROTECT, verbose_name="续学题目")
    round = models.ForeignKey(PracticeRound, on_delete=models.PROTECT, null=True, blank=True, verbose_name="轮次")
    group_index = models.PositiveIntegerField("组号", null=True, blank=True)
    version = models.PositiveIntegerField("版本", default=1)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "续学位置"
        verbose_name_plural = verbose_name
        constraints = [
            models.CheckConstraint(condition=scope_condition(), name="practice_resume_scope"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="practice_resume_version"),
            models.CheckConstraint(condition=models.Q(mode__in=("writing", "reciting")), name="practice_resume_mode"),
            models.CheckConstraint(condition=(models.Q(round__isnull=True, group_index__isnull=True) | models.Q(round__isnull=False, group_index__isnull=False, group_index__gte=1)), name="practice_resume_random_context"),
        ]
