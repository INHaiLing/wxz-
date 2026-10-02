import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from entitlements.models import SCOPE, ServiceOnlyModel


def order_id():
    return uuid.uuid4().hex


class Order(ServiceOnlyModel):
    STATES = tuple((v, label) for v, label in (
        ("created", "本地待支付"), ("preparing", "平台处理中"), ("paid", "已付款待发权"),
        ("fulfilled", "已开通"), ("closed", "平台确认关闭"), ("refunded", "平台已退款"), ("review", "待人工核查")))
    id = models.CharField(primary_key=True, max_length=32, default=order_id, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payment_orders")
    identity = models.ForeignKey("accounts.WeChatIdentity", on_delete=models.PROTECT)
    product = models.ForeignKey("entitlements.Product", on_delete=models.PROTECT)
    product_name = models.CharField(max_length=120)
    scope = models.CharField(max_length=32, default=SCOPE)
    price_fen = models.PositiveIntegerField()
    platform_product_id = models.CharField(max_length=128)
    app_id = models.CharField(max_length=64)
    environment = models.PositiveSmallIntegerField(default=0)
    channel = models.CharField(max_length=16, choices=(("android", "Android"), ("ios", "iOS")))
    status = models.CharField(max_length=16, choices=STATES, default="created")
    prepared_at = models.DateTimeField(null=True, blank=True)
    prepared_session = models.ForeignKey("accounts.StudentSession", on_delete=models.PROTECT, null=True, blank=True)
    sign_data = models.TextField(blank=True)
    configuration_digest = models.CharField(max_length=64, blank=True)
    platform_order_id = models.CharField(max_length=128, blank=True)
    transaction_id = models.CharField(max_length=128, blank=True)
    last_platform_status = models.IntegerField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    review_reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "付款订单"
        verbose_name_plural = verbose_name
        ordering = ("-created_at", "id")
        constraints = (
            models.CheckConstraint(condition=models.Q(price_fen__gt=0), name="payment_positive_price"),
            models.CheckConstraint(condition=models.Q(scope=SCOPE), name="payment_single_scope"),
            models.CheckConstraint(condition=models.Q(environment__in=(0, 1)), name="payment_environment"),
        )


class PaymentTask(ServiceOnlyModel):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="tasks")
    kind = models.CharField(max_length=16, choices=(("query", "查单"), ("deliver", "通知发货")))
    status = models.CharField(max_length=16, choices=(("pending", "待执行"), ("running", "执行中"), ("done", "完成"), ("failed", "需核查")), default="pending")
    attempts = models.PositiveIntegerField(default=0)
    next_run_at = models.DateTimeField(default=timezone.now)
    lease_token = models.CharField(max_length=64, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "支付补偿任务"
        verbose_name_plural = verbose_name
        constraints = (models.UniqueConstraint(fields=("order", "kind"), name="payment_unique_task"),)
