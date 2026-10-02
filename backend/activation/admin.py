import csv
import uuid
from io import StringIO

from django.contrib import admin, messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.views.decorators.debug import sensitive_variables
from unfold.admin import ModelAdmin

from common.errors import BusinessError
from entitlements.admin import ReadOnlyBusinessAdmin
from .forms import GenerateBatchForm
from .models import ActivationBatch, ActivationCode, ActivationRedemption
from .services import (
    TRANSITIONS, batch_fingerprint, code_fingerprint, disable_code,
    generate_batch, transition_batch,
)


SIGNING_SALT = "activation.admin.confirmation.v1"
CONFIRMATION_SECONDS = 1800
ACTION_LABELS = {"receive": "确认文件已领取", "enable": "启用批次", "disable": "禁用未兑码", "void": "作废未领取批次"}


def _sign(request, object_id, action, fingerprint):
    return signing.dumps({"owner": str(request.user.pk), "id": str(object_id), "action": action, "fingerprint": fingerprint}, salt=SIGNING_SALT)


def _read_signature(request, object_id, action):
    try:
        payload = signing.loads(request.POST.get("confirmation_token", ""), salt=SIGNING_SALT, max_age=CONFIRMATION_SECONDS)
    except (signing.BadSignature, ValueError, TypeError) as error:
        raise BusinessError("VERSION_CONFLICT", "确认凭证无效或已过期，请重新预览。", 409) from error
    if not isinstance(payload, dict) or payload.get("owner") != str(request.user.pk) or payload.get("id") != str(object_id) or payload.get("action") != action or not isinstance(payload.get("fingerprint"), str):
        raise BusinessError("VERSION_CONFLICT", "确认凭证与当前操作不匹配，请重新预览。", 409)
    if request.POST.get("confirm") != "yes":
        raise BusinessError("CONFIRMATION_REQUIRED", "请核对影响并确认操作。")
    return payload["fingerprint"]


@admin.register(ActivationBatch)
class ActivationBatchAdmin(ReadOnlyBusinessAdmin):
    list_display = ("id", "label", "quantity", "state", "created_by", "created_at", "action_links")
    list_filter = ("state",)
    list_select_related = ("created_by", "received_by")
    search_fields = ("label", "created_by__username")
    change_list_template = "admin/activation/activationbatch/change_list.html"

    def get_readonly_fields(self, request, obj=None):
        return (*super().get_readonly_fields(request, obj), "action_links", "codes_link")

    def get_urls(self):
        return [
            path("generate/", self.admin_site.admin_view(self.generate_view), name="activation_batch_generate"),
            path("<uuid:batch_id>/transition/<str:action>/", self.admin_site.admin_view(self.transition_view), name="activation_batch_transition"),
        ] + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        extra_context = {**(extra_context or {}), "can_generate": request.user.has_perm("activation.generate_activationbatch"), "generate_url": reverse("admin:activation_batch_generate")}
        return super().changelist_view(request, extra_context)

    @admin.display(description="批次操作")
    def action_links(self, obj):
        available = {"awaiting_receipt": ("receive", "void"), "received": ("enable",), "enabled": ("disable",), "disabled": ("enable",), "void": ()}
        return format_html_join(" · ", '<a href="{}">{}</a>', ((reverse("admin:activation_batch_transition", args=(obj.pk, action)), ACTION_LABELS[action]) for action in available[obj.state])) or "已作废不可恢复"

    @admin.display(description="激活码记录")
    def codes_link(self, obj):
        return format_html('<a href="{}?batch__id__exact={}">查看本批掩码与兑换状态</a>', reverse("admin:activation_activationcode_changelist"), obj.pk)

    @sensitive_variables("raw_codes", "buffer", "writer", "raw", "response")
    def generate_view(self, request):
        if not self.has_view_permission(request) or not request.user.has_perm("activation.generate_activationbatch"):
            raise PermissionDenied("没有生成激活码的权限。")
        form = GenerateBatchForm(request.POST or None, initial={"idempotency_key": uuid.uuid4().hex})
        context = {**self.admin_site.each_context(request), "opts": self.model._meta, "title": "生成并一次下载激活码", "media": self.media, "form": form, "back_url": reverse("admin:activation_activationbatch_changelist")}
        if request.method == "POST" and form.is_valid():
            try:
                batch, raw_codes = generate_batch(request.user, form.cleaned_data["quantity"], form.cleaned_data["label"], form.cleaned_data["idempotency_key"])
            except BusinessError as error:
                form.add_error(None, str(error.detail["error"]["message"]))
                return TemplateResponse(request, "admin/activation/generate.html", context, status=error.status_code)
            if raw_codes is None:
                self.message_user(request, "该请求的批次已生成，原码不能再次下载。若文件未保存，请作废待领取批次后重新生成。", messages.WARNING)
                return HttpResponseRedirect(reverse("admin:activation_activationbatch_change", args=(batch.pk,)))
            buffer = StringIO(newline="")
            writer = csv.writer(buffer)
            writer.writerow(("序号", "永久激活码"))
            for index, raw in enumerate(raw_codes, 1):
                writer.writerow((index, raw))
            response = HttpResponse("\ufeff" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="activation-{batch.pk}.csv"'
            response["Cache-Control"] = "no-store, private"
            response["X-Activation-Batch-ID"] = str(batch.pk)
            return response
        return TemplateResponse(request, "admin/activation/generate.html", context, status=400 if request.method == "POST" else 200)

    def transition_view(self, request, batch_id, action):
        batch = get_object_or_404(ActivationBatch, pk=batch_id)
        if action not in TRANSITIONS or not self.has_view_permission(request, batch) or not request.user.has_perm("activation." + TRANSITIONS[action][2]):
            raise PermissionDenied("没有此批次操作权限。")
        context = {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": ACTION_LABELS[action], "media": self.media,
            "back_url": reverse("admin:activation_activationbatch_change", args=(batch.pk,)),
            "details": (("批次", batch.pk), ("当前状态", batch.get_state_display()), ("未兑码", batch.codes.filter(state="unused").count()), ("已兑码", batch.codes.filter(state="redeemed").count())),
            "description": "领取确认代表 CSV 已妥善保存；系统无法重新提供原码。启用后未兑码可兑换，禁用和作废不会撤销已兑权益。",
            "need_reason": action in ("disable", "void"),
        }
        if request.method == "POST":
            try:
                fingerprint = _read_signature(request, batch.pk, action)
                transition_batch(batch.pk, request.user, action, expected_fingerprint=fingerprint, reason=request.POST.get("reason", ""))
            except BusinessError as error:
                context["error"] = str(error.detail["error"]["message"])
                return TemplateResponse(request, "admin/activation/confirmation.html", context, status=error.status_code)
            self.message_user(request, "已完成批次操作；原码不会再次导出。", messages.SUCCESS)
            return HttpResponseRedirect(context["back_url"])
        context["confirmation_token"] = _sign(request, batch.pk, action, batch_fingerprint(batch))
        return TemplateResponse(request, "admin/activation/confirmation.html", context)


@admin.register(ActivationCode)
class ActivationCodeAdmin(ReadOnlyBusinessAdmin):
    list_display = ("id", "batch", "mask", "state", "redeemed_by", "redeemed_at", "action_link")
    list_filter = ("state", "batch__state")
    list_select_related = ("batch", "redeemed_by")
    search_fields = ("mask", "redeemed_by__username")
    fields = ("id", "batch", "mask", "state", "redeemed_by", "redeemed_at", "disabled_at", "reason", "action_link")

    def get_readonly_fields(self, request, obj=None):
        return self.fields

    def get_urls(self):
        return [path("<uuid:code_id>/disable/", self.admin_site.admin_view(self.disable_view), name="activation_code_disable")] + super().get_urls()

    @admin.display(description="受控操作")
    def action_link(self, obj):
        if obj.state == "unused":
            return format_html('<a href="{}">预览并禁用此码</a>', reverse("admin:activation_code_disable", args=(obj.pk,)))
        if obj.state == "redeemed":
            redemption = ActivationRedemption.objects.filter(code=obj).first()
            if redemption:
                return format_html('<a href="{}">查看对应权益及误发撤权</a>', reverse("admin:entitlements_entitlementsource_change", args=(redemption.source_id,)))
        return "保留历史，不可重新使用"

    def disable_view(self, request, code_id):
        code = get_object_or_404(ActivationCode, pk=code_id)
        if not self.has_view_permission(request, code) or not request.user.has_perm("activation.disable_activationcode"):
            raise PermissionDenied("没有单码禁用权限。")
        context = {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "预览并禁用未兑码", "media": self.media,
            "back_url": reverse("admin:activation_activationcode_change", args=(code.pk,)),
            "details": (("码记录编号", code.pk), ("掩码", code.mask), ("当前状态", code.get_state_display())),
            "description": "禁用仅适用于未兑换码。已兑换码需要在权益来源页面确认误发撤权，使用历史仍保留。", "need_reason": True,
        }
        if request.method == "POST":
            try:
                fingerprint = _read_signature(request, code.pk, "disable_code")
                disable_code(code.pk, request.user, request.POST.get("reason", ""), expected_fingerprint=fingerprint)
            except BusinessError as error:
                context["error"] = str(error.detail["error"]["message"])
                return TemplateResponse(request, "admin/activation/confirmation.html", context, status=error.status_code)
            self.message_user(request, "已禁用此未兑码。", messages.SUCCESS)
            return HttpResponseRedirect(context["back_url"])
        context["confirmation_token"] = _sign(request, code.pk, "disable_code", code_fingerprint(code))
        return TemplateResponse(request, "admin/activation/confirmation.html", context)


@admin.register(ActivationRedemption)
class ActivationRedemptionAdmin(ReadOnlyBusinessAdmin):
    list_display = ("id", "code", "user", "source", "redeemed_at")
    list_select_related = ("code", "user", "source")
    search_fields = ("user__username", "code__mask")
