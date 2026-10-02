"""Permanent sources and durable opening reservations; changes use services."""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone


SCOPE = "all_chinese"
SCOPE_CHOICES = ((SCOPE, "全部语文题库"),)


class ServiceOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("业务记录只能通过权益服务修改。")

    def bulk_update(self, *args, **kwargs):
        raise ValidationError("业务记录不能批量覆盖。")

    def bulk_create(self, *args, **kwargs):
        raise ValidationError("业务记录不能绕过服务批量创建。")

    def delete(self):
        raise ValidationError("业务记录必须保留，不能删除。")


class ServiceOnlyModel(models.Model):
    objects = ServiceOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, _service=False, **kwargs):
        if not _service:
            raise ValidationError("业务记录只能通过权益服务写入。")
        # Unique/constraint decisions belong to the database under concurrency.
        # Field and model validation still run; pre-checking uniqueness could
        # turn a competing insert into an unhandled ValidationError.
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("业务记录必须保留，不能删除。")


class Product(models.Model):
    id = models.CharField("商品编号", primary_key=True, max_length=64, default="all-chinese")
    scope = models.CharField("权益范围", max_length=32, choices=SCOPE_CHOICES, default=SCOPE, unique=True)
    name = models.CharField("商品名称", max_length=120, default="语文全题库永久权益")
    price_fen = models.PositiveIntegerField("金额（分）", default=1000, validators=(MinValueValidator(1),))
    is_active = models.BooleanField("启用", default=True)
    platform_product_id = models.CharField("平台商品编号", max_length=128, blank=True)
    platform_synced_price_fen = models.PositiveIntegerField("最近同步金额（分）", null=True, blank=True)
    platform_sync_state = models.CharField("同步状态", max_length=16, choices=(("pending", "待同步"), ("synced", "已同步")), default="pending")
    platform_synced_at = models.DateTimeField("同步确认时间", null=True, blank=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "全题库商品"
        verbose_name_plural = verbose_name
        permissions = (("sync_product", "可以确认平台商品价格已同步"),)
        constraints = (
            models.CheckConstraint(condition=models.Q(scope=SCOPE), name="ent_product_scope"),
            models.CheckConstraint(condition=models.Q(price_fen__gt=0), name="ent_product_positive_price"),
        )

    def __str__(self):
        return self.name

    def save(self, *args, _sync=False, **kwargs):
        with transaction.atomic():
            previous = type(self).objects.select_for_update().filter(pk=self.pk).first()
            if previous:
                changed = previous.price_fen != self.price_fen or previous.platform_product_id != self.platform_product_id
                if changed:
                    self.platform_sync_state = "pending"
                    self.platform_synced_at = None
                elif not _sync:
                    self.platform_sync_state = previous.platform_sync_state
                    self.platform_synced_at = previous.platform_synced_at
                    self.platform_synced_price_fen = previous.platform_synced_price_fen
            elif not _sync:
                self.platform_sync_state = "pending"
                self.platform_synced_at = None
                self.platform_synced_price_fen = None
            self.full_clean()
            return super().save(*args, **kwargs)


class EntitlementSource(ServiceOnlyModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="entitlement_sources", verbose_name="学员")
    scope = models.CharField("权益范围", max_length=32, choices=SCOPE_CHOICES, default=SCOPE)
    source_type = models.CharField("来源类型", max_length=16, choices=(("activation", "激活码"), ("payment", "付款")))
    source_id = models.CharField("来源业务编号", max_length=128)
    expires_at = models.DateTimeField("到期时间（永久为空）", null=True, blank=True)
    status = models.CharField("来源状态", max_length=16, choices=(("active", "有效"), ("revoked", "已撤销")), default="active")
    created_at = models.DateTimeField("发放时间", auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="issued_entitlement_sources", verbose_name="发放操作者")
    revoked_at = models.DateTimeField("撤销时间", null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="revoked_entitlement_sources", verbose_name="撤销操作者")
    reason = models.CharField("撤销理由", max_length=500, blank=True)

    class Meta:
        verbose_name = "永久权益来源"
        verbose_name_plural = verbose_name
        ordering = ("-created_at", "id")
        permissions = (
            ("grant_entitlementsource", "可以通过受控服务发放永久权益"),
            ("revoke_entitlementsource", "可以撤销指定永久权益来源"),
        )
        constraints = (
            models.UniqueConstraint(fields=("source_type", "source_id"), name="ent_unique_source"),
            models.CheckConstraint(condition=models.Q(expires_at__isnull=True), name="ent_permanent_no_expiry"),
            models.CheckConstraint(condition=models.Q(scope=SCOPE), name="ent_source_scope"),
            models.CheckConstraint(condition=models.Q(source_type__in=("activation", "payment")), name="ent_source_type"),
            models.CheckConstraint(condition=(models.Q(status="active", revoked_at__isnull=True) | models.Q(status="revoked", revoked_at__isnull=False)), name="ent_source_status"),
        )

    def __str__(self):
        return f"{self.user_id} · {self.get_source_type_display()} · {self.source_id}"


class OpeningReservation(ServiceOnlyModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="opening_reservations", verbose_name="学员")
    scope = models.CharField("权益范围", max_length=32, choices=SCOPE_CHOICES, default=SCOPE)
    order_id = models.CharField("本地订单编号", max_length=128)
    reserved_at = models.DateTimeField("占位时间", default=timezone.now)
    released_at = models.DateTimeField("解除时间", null=True, blank=True)
    release_reason = models.CharField("解除依据", max_length=32, blank=True)

    class Meta:
        verbose_name = "支付开通占位"
        verbose_name_plural = verbose_name
        constraints = (
            models.UniqueConstraint(fields=("user", "scope"), name="ent_unique_user_reservation"),
            models.CheckConstraint(condition=models.Q(scope=SCOPE), name="ent_reservation_scope"),
        )

    @property
    def is_active(self):
        return self.released_at is None


class AuditEvent(ServiceOnlyModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField("操作", max_length=32)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="entitlement_events", verbose_name="学员")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="performed_entitlement_events", verbose_name="操作者")
    source = models.ForeignKey(EntitlementSource, on_delete=models.PROTECT, null=True, blank=True, related_name="events", verbose_name="权益来源")
    order_id = models.CharField("本地订单编号", max_length=128, blank=True)
    details = models.JSONField("业务记录", default=dict)
    created_at = models.DateTimeField("时间", auto_now_add=True)

    class Meta:
        verbose_name = "权益业务审计"
        verbose_name_plural = verbose_name
        ordering = ("-created_at", "id")

    def save(self, *args, _service=False, **kwargs):
        if not self._state.adding:
            raise ValidationError("审计创建后不可修改。")
        return super().save(*args, _service=_service, **kwargs)
