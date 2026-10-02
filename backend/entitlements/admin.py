"""Business rows are read-only; controlled actions have signed impact previews."""

from django.contrib import admin, messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from common.errors import BusinessError
from .models import AuditEvent, EntitlementSource, OpeningReservation, Product
from .services import (
    _audit, mark_product_synced, product_fingerprint, revoke_entitlement,
    revocation_preview,
)


CONFIRMATION_SECONDS = 1800
SIGNING_SALT = "entitlements.admin.action.v1"


def _confirmation_token(request, action, object_id, fingerprint):
    return signing.dumps({"owner": str(request.user.pk), "action": action, "id": str(object_id), "fingerprint": fingerprint}, salt=SIGNING_SALT)


def _read_token(request, action, object_id):
    try:
        payload = signing.loads(request.POST.get("confirmation_token", ""), salt=SIGNING_SALT, max_age=CONFIRMATION_SECONDS)
    except (signing.BadSignature, ValueError, TypeError) as error:
        raise BusinessError("VERSION_CONFLICT", "确认凭证无效或已过期，请重新预览。", 409) from error
    if not isinstance(payload, dict) or payload.get("owner") != str(request.user.pk) or payload.get("action") != action or payload.get("id") != str(object_id) or not isinstance(payload.get("fingerprint"), str):
        raise BusinessError("VERSION_CONFLICT", "确认凭证不属于本次操作，请重新预览。", 409)
    if request.POST.get("confirm") != "yes":
        raise BusinessError("CONFIRMATION_REQUIRED", "请先核对本次操作影响并确认。")
    return payload["fingerprint"]


class ReadOnlyBusinessAdmin(ModelAdmin):
    actions = None

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Product)
class ProductAdmin(ModelAdmin):
    list_display = ("id", "name", "price_fen", "is_active", "platform_sync_state", "sync_link")
    readonly_fields = ("scope", "platform_sync_state", "platform_synced_price_fen", "platform_synced_at", "updated_at", "sync_link")
    fields = ("id", "scope", "name", "price_fen", "is_active", "platform_product_id", "platform_sync_state", "platform_synced_price_fen", "platform_synced_at", "updated_at", "sync_link")
    actions = None

    def has_add_permission(self, request):
        return False  # seed_product owns the single product identity.

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return (*self.readonly_fields, "id")

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            original = Product.objects.select_for_update().get(pk=obj.pk)
            before = {"name": original.name, "priceFen": original.price_fen, "active": original.is_active, "platformProductId": original.platform_product_id}
            super().save_model(request, obj, form, change)
            after = {"name": obj.name, "priceFen": obj.price_fen, "active": obj.is_active, "platformProductId": obj.platform_product_id}
            if before != after:
                _audit("product_changed", actor=request.user, productId=obj.pk, before=before, after=after)

    @admin.display(description="平台同步确认")
    def sync_link(self, obj):
        if not obj or not obj.pk:
            return "—"
        return format_html('<a href="{}">核对平台商品与金额</a>', reverse("admin:entitlements_product_sync", args=(obj.pk,)))

    def get_urls(self):
        return [path("<str:product_id>/sync/", self.admin_site.admin_view(self.sync_view), name="entitlements_product_sync")] + super().get_urls()

    def sync_view(self, request, product_id):
        product = get_object_or_404(Product, pk=product_id)
        if not self.has_view_permission(request, product) or not request.user.has_perm("entitlements.sync_product"):
            raise PermissionDenied("没有确认平台同步的权限。")
        context = {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "确认平台商品同步", "media": self.media,
            "back_url": reverse("admin:entitlements_product_change", args=(product.pk,)),
            "details": (("商品", product.name), ("金额（分）", product.price_fen), ("平台商品编号", product.platform_product_id or "未配置")),
            "description": "请先在微信平台核对该商品及金额，再确认已同步。此标记不代表真实支付验收完成。", "need_reason": False,
        }
        if request.method == "POST":
            try:
                fingerprint = _read_token(request, "sync", product.pk)
                mark_product_synced(product.pk, request.user, price_fen=product.price_fen, platform_product_id=product.platform_product_id, expected_fingerprint=fingerprint)
            except BusinessError as error:
                context["error"] = str(error.detail["error"]["message"])
                return TemplateResponse(request, "admin/entitlements/confirmation.html", context, status=error.status_code)
            self.message_user(request, "已记录平台同步确认。", messages.SUCCESS)
            return self.response_post_save_change(request, product)
        context["confirmation_token"] = _confirmation_token(request, "sync", product.pk, product_fingerprint(product))
        return TemplateResponse(request, "admin/entitlements/confirmation.html", context)


@admin.register(EntitlementSource)
class EntitlementSourceAdmin(ReadOnlyBusinessAdmin):
    list_display = ("user", "source_type", "source_id", "status", "created_at", "revoked_at", "revoke_link")
    list_filter = ("source_type", "status")
    search_fields = ("source_id", "user__username")
    list_select_related = ("user", "created_by", "revoked_by")

    def get_readonly_fields(self, request, obj=None):
        return (*super().get_readonly_fields(request, obj), "revoke_link")

    @admin.display(description="撤销操作")
    def revoke_link(self, obj):
        if not obj or obj.status == "revoked":
            return "已撤销（保留历史）"
        if obj.source_type == "payment":
            return "付款来源由平台退款核验同步处理"
        return format_html('<a href="{}">预览并撤销此来源</a>', reverse("admin:entitlements_source_revoke", args=(obj.pk,)))

    def get_urls(self):
        return [path("<uuid:source_id>/revoke/", self.admin_site.admin_view(self.revoke_view), name="entitlements_source_revoke")] + super().get_urls()

    def revoke_view(self, request, source_id):
        source = get_object_or_404(EntitlementSource, pk=source_id)
        if not self.has_view_permission(request, source) or not request.user.has_perm("entitlements.revoke_entitlementsource"):
            raise PermissionDenied("没有撤销权益来源的权限。")
        if source.source_type != "activation":
            raise PermissionDenied("后台误发撤权仅适用于激活码来源；付款来源通过平台退款核验同步处理。")
        preview = revocation_preview(source)
        context = {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "预览并撤销永久权益来源", "media": self.media,
            "back_url": reverse("admin:entitlements_entitlementsource_change", args=(source.pk,)),
            "details": (("学员编号", source.user_id), ("来源类型", source.get_source_type_display()), ("来源业务编号", source.source_id), ("剩余有效来源", preview["remainingSourceCount"]), ("撤销后", "仍有永久权益" if preview["willRemainActive"] else "仅可访问免费试学范围")),
            "description": "仅撤销本次来源；订单、已兑换码、学习记录及历史仍保留。", "need_reason": True,
            "reason": request.POST.get("reason", ""),
        }
        if request.method == "POST":
            try:
                fingerprint = _read_token(request, "revoke", source.pk)
                revoke_entitlement(source.pk, request.user, request.POST.get("reason", ""), expected_fingerprint=fingerprint)
            except BusinessError as error:
                context["error"] = str(error.detail["error"]["message"])
                return TemplateResponse(request, "admin/entitlements/confirmation.html", context, status=error.status_code)
            self.message_user(request, "已撤销指定来源，其他有效来源不受影响。", messages.SUCCESS)
            return self.response_post_save_change(request, source)
        context["confirmation_token"] = _confirmation_token(request, "revoke", source.pk, preview["fingerprint"])
        return TemplateResponse(request, "admin/entitlements/confirmation.html", context)


@admin.register(OpeningReservation)
class OpeningReservationAdmin(ReadOnlyBusinessAdmin):
    list_display = ("user", "scope", "order_id", "reserved_at", "released_at", "release_reason")
    search_fields = ("order_id", "user__username")
    list_select_related = ("user",)


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyBusinessAdmin):
    list_display = ("kind", "user", "actor", "created_at")
    list_filter = ("kind",)
    list_select_related = ("user", "actor", "source")
    search_fields = ("order_id", "source__source_id", "user__username")
