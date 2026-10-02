from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class QuestionState(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    question = models.ForeignKey("content.Question", on_delete=models.PROTECT)
    favorite = models.BooleanField("收藏", default=False)
    mastered = models.BooleanField("掌握", default=False)
    version = models.PositiveBigIntegerField("版本", default=0)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "学习状态"
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=("user", "question"), name="learning_unique_question_state")]


class LearningActivity(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    question = models.ForeignKey("content.Question", on_delete=models.PROTECT)
    study_date = models.DateField("上海学习日期", db_index=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "学习活动"
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=("user", "question", "study_date"), name="learning_unique_daily_activity")]


class LearningPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    mode = models.CharField("学习模式", max_length=10, choices=(("writing", "默写"), ("reciting", "背诵")), default="writing")
    daily_target = models.PositiveSmallIntegerField("每日目标", default=20, validators=(MinValueValidator(1), MaxValueValidator(1000)))
    version = models.PositiveBigIntegerField("版本", default=0)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "学习偏好"
        verbose_name_plural = verbose_name
        constraints = [
            models.CheckConstraint(condition=models.Q(mode__in=("writing", "reciting")), name="learning_valid_mode"),
            models.CheckConstraint(condition=models.Q(daily_target__gte=1, daily_target__lte=1000), name="learning_valid_target"),
        ]
