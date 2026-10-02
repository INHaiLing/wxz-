import uuid

from django.conf import settings
from django.db import models

from entitlements.models import ServiceOnlyModel


class ActivationBatch(ServiceOnlyModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quantity = models.PositiveIntegerField("数量")
    label = models.CharField("用途说明", max_length=120, blank=True)
    state = models.CharField("批次状态", max_length=24, choices=(("awaiting_receipt", "待领取确认"), ("received", "已领取待启用"), ("enabled", "已启用"), ("disabled", "已禁用"), ("void", "已作废")), default="awaiting_receipt")
    version = models.PositiveIntegerField("状态版本", default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="activation_batches", verbose_name="创建者")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="received_activation_batches", verbose_name="领取确认者")
    received_at = models.DateTimeField("领取确认时间", null=True, blank=True)
    updated_at = models.DateTimeField("状态更新时间", auto_now=True)

    class Meta:
        verbose_name = "激活码批次"
        verbose_name_plural = verbose_name
        ordering = ("-created_at", "id")
        permissions = (
            ("generate_activationbatch", "可以生成并一次下载激活码"),
            ("receive_activationbatch", "可以确认激活码文件已领取"),
            ("enable_activationbatch", "可以启用已领取批次"),
            ("disable_activationbatch", "可以禁用激活码批次"),
            ("void_activationbatch", "可以作废未领取批次"),
        )
        constraints = (
            models.CheckConstraint(condition=models.Q(quantity__gte=1, quantity__lte=500), name="activation_batch_quantity"),
            models.CheckConstraint(condition=models.Q(state__in=("awaiting_receipt", "received", "enabled", "disabled", "void")), name="activation_batch_state"),
        )

    def __str__(self):
        return f"{str(self.pk)[:8]} · {self.quantity} 个 · {self.get_state_display()}"


class ActivationCode(ServiceOnlyModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(ActivationBatch, on_delete=models.PROTECT, related_name="codes", verbose_name="批次")
    digest = models.CharField("激活码摘要", max_length=64, unique=True)
    mask = models.CharField("掩码", max_length=32)
    state = models.CharField("使用状态", max_length=16, choices=(("unused", "未使用"), ("redeemed", "已兑换"), ("disabled", "单码禁用")), default="unused")
    redeemed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="redeemed_activation_codes", verbose_name="兑换学员")
    redeemed_at = models.DateTimeField("兑换时间", null=True, blank=True)
    disabled_at = models.DateTimeField("单码禁用时间", null=True, blank=True)
    reason = models.CharField("禁用理由", max_length=500, blank=True)

    class Meta:
        verbose_name = "激活码（仅掩码）"
        verbose_name_plural = verbose_name
        permissions = (("disable_activationcode", "可以禁用未兑激活码"),)
        constraints = (
            models.CheckConstraint(condition=(models.Q(state="redeemed", redeemed_by__isnull=False, redeemed_at__isnull=False) | models.Q(state__in=("unused", "disabled"), redeemed_by__isnull=True, redeemed_at__isnull=True)), name="activation_code_used_state"),
        )

    def __str__(self):
        return self.mask


class ActivationRedemption(ServiceOnlyModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.OneToOneField(ActivationCode, on_delete=models.PROTECT, related_name="redemption", verbose_name="已兑码")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="activation_redemptions", verbose_name="学员")
    source = models.OneToOneField("entitlements.EntitlementSource", on_delete=models.PROTECT, related_name="activation_redemption", verbose_name="永久权益来源")
    redeemed_at = models.DateTimeField("兑换时间", auto_now_add=True)

    class Meta:
        verbose_name = "激活码兑换历史"
        verbose_name_plural = verbose_name
        ordering = ("-redeemed_at", "id")
