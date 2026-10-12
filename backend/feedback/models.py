from django.conf import settings
from django.core.validators import MaxLengthValidator
from django.db import models


class FeedbackType(models.TextChoices):
    FEATURE = "feature", "功能问题"
    QUESTION = "question", "题目问题"
    PAYMENT = "payment", "支付问题"
    ACCOUNT = "account", "账号问题"
    OTHER = "other", "其他"


class FeedbackStatus(models.TextChoices):
    PENDING = "pending", "待处理"
    PROCESSING = "processing", "处理中"
    RESOLVED = "resolved", "已处理"
    CLOSED = "closed", "已关闭"


class Feedback(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="feedback_submissions",
        verbose_name="学员",
    )
    feedback_type = models.CharField("反馈类型", max_length=20, choices=FeedbackType.choices)
    content = models.TextField("问题描述", max_length=5000, validators=(MaxLengthValidator(5000),))
    contact = models.CharField("联系方式", max_length=200, blank=True)
    status = models.CharField("处理状态", max_length=20, choices=FeedbackStatus.choices, default=FeedbackStatus.PENDING)
    admin_note = models.TextField("处理备注", max_length=5000, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="handled_feedback",
        verbose_name="处理人",
    )
    handled_at = models.DateTimeField("处理时间", null=True, blank=True)
    created_at = models.DateTimeField("提交时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "意见反馈"
        verbose_name_plural = "意见反馈"
        ordering = ("-created_at", "-id")
        default_permissions = ("view",)
        permissions = (("process_feedback", "可以处理意见反馈"),)

    def __str__(self):
        return f"反馈 #{self.pk} · {self.get_feedback_type_display()} · {self.get_status_display()}"
