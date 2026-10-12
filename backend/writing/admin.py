from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.utils import quote, unquote
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from .forms import WritingContentAdminForm
from .models import WritingCategory, WritingContent, WritingGenre, WritingRevision
from .services import (
    ContentConflict,
    make_publication_token,
    publish_writings,
    require_publish_permission,
    save_writing_draft,
    unpublish_writings,
)


class RetainedAdmin(ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        return fields + (("id",) if obj else ())


@admin.register(WritingCategory)
class WritingCategoryAdmin(RetainedAdmin):
    list_display = ("id", "name", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("id", "name")
    fields = ("id", "name", "sort_order", "is_active")


@admin.register(WritingGenre)
class WritingGenreAdmin(RetainedAdmin):
    list_display = ("id", "name", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("id", "name")
    fields = ("id", "name", "sort_order", "is_active")


@admin.register(WritingContent)
class WritingContentAdmin(RetainedAdmin):
    form = WritingContentAdminForm
    list_display = ("id", "title", "category", "genre", "kind", "publication_status", "sort_order", "updated_at")
    list_filter = ("kind", "is_published", "category", "genre")
    search_fields = ("id", "title", "body", "reference_answer")
    list_select_related = ("category", "genre", "published_revision")
    readonly_fields = ("publication_status", "published_version", "preview_link", "created_at", "updated_at")
    actions = ("publish_selected", "unpublish_selected")
    fieldsets = (
        ("应用文归属", {"fields": ("id", "category", "genre", "kind", "sort_order")}),
        ("草稿内容", {"fields": ("title", "body", "reference_answer", "draft_token"), "description": "保存只更新草稿；发布后学员接口才会读取新内容。"}),
        ("发布信息", {"fields": ("publication_status", "published_version", "preview_link", "created_at", "updated_at")}),
    )

    @admin.display(description="发布状态")
    def publication_status(self, obj):
        if not obj or not obj.pk:
            return "未发布"
        if not obj.is_published:
            return "已下架（保留历史）" if obj.published_revision_id else "草稿"
        return "已发布 · 有未发布修改" if obj.has_unpublished_changes else "已发布"

    @admin.display(description="当前发布版本")
    def published_version(self, obj):
        return f"v{obj.published_revision.version}" if obj and obj.published_revision_id else "尚未发布"

    @admin.display(description="预览")
    def preview_link(self, obj):
        if not obj or obj._state.adding:
            return "保存后可预览"
        url = reverse("admin:writing_writingcontent_preview", args=(quote(obj.pk),))
        return format_html('<a href="{}">查看内容预览</a>', url)

    def get_form(self, request, obj=None, **kwargs):
        base_form = super().get_form(request, obj, **kwargs)

        class RequestBoundForm(base_form):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, request=request, **form_kwargs)

        return RequestBoundForm

    def has_publish_permission(self, request):
        try:
            require_publish_permission(request.user)
        except PermissionDenied:
            return False
        return True

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            response = super().changeform_view(request, object_id, form_url, extra_context)
        except (ContentConflict, ValidationError) as error:
            request._draft_conflict = "；".join(error.messages)
            response = super().changeform_view(request, object_id, form_url, extra_context)
        if getattr(request, "_draft_conflict", None) and isinstance(response, TemplateResponse):
            response.status_code = 409
        return response

    def _changeform_view(self, request, object_id, form_url, extra_context):
        try:
            with transaction.atomic():
                return super()._changeform_view(request, object_id, form_url, extra_context)
        except ValidationError as error:
            request._draft_conflict = "；".join(error.messages)
            return super()._changeform_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        if change:
            save_writing_draft(obj, request.user, form.cleaned_data.get("draft_token"))
        else:
            super().save_model(request, obj, form, change)

    def get_urls(self):
        return [path("<path:object_id>/preview/", self.admin_site.admin_view(self.preview_view), name="writing_writingcontent_preview")] + super().get_urls()

    def preview_view(self, request, object_id):
        content = self.get_object(request, unquote(object_id))
        if content is None:
            raise PermissionDenied("应用文不存在。")
        if not self.has_view_permission(request, content):
            raise PermissionDenied("当前账户没有查看应用文的权限。")
        return TemplateResponse(request, "admin/writing/writingcontent/preview.html", {
            **self.admin_site.each_context(request),
            "title": "应用文预览", "opts": self.model._meta, "content": content,
            "status": self.publication_status(content),
            "change_url": reverse("admin:writing_writingcontent_change", args=(quote(content.pk),)),
        })

    def _publication_action(self, request, queryset, *, publish):
        require_publish_permission(request.user)
        action = "publish_selected" if publish else "unpublish_selected"
        contents = list(queryset.order_by("pk"))
        if request.POST.get("confirm_publication") != action:
            return TemplateResponse(request, "admin/writing/writingcontent/confirm_publication.html", {
                **self.admin_site.each_context(request), "title": "确认发布应用文" if publish else "确认下架应用文",
                "opts": self.model._meta, "contents": contents, "action_name": action,
                "checkbox_name": ACTION_CHECKBOX_NAME, "publishing": publish,
                "confirmation_token": make_publication_token(contents, request.user, action),
            })
        try:
            with transaction.atomic():
                service = publish_writings if publish else unpublish_writings
                count = service(queryset, request.user, confirmation_token=request.POST.get("confirmation_token", ""))
        except (ContentConflict, ValidationError) as error:
            self.message_user(request, "整批操作未生效：" + "；".join(error.messages), level=messages.ERROR)
        else:
            self.message_user(request, f"已{'发布' if publish else '下架'} {count} 条应用文。", level=messages.SUCCESS)

    @admin.action(description="发布所选应用文", permissions=("publish",))
    def publish_selected(self, request, queryset):
        return self._publication_action(request, queryset, publish=True)

    @admin.action(description="下架所选应用文", permissions=("publish",))
    def unpublish_selected(self, request, queryset):
        return self._publication_action(request, queryset, publish=False)


@admin.register(WritingRevision)
class WritingRevisionAdmin(ModelAdmin):
    list_display = ("content", "version", "kind", "created_by", "created_at")
    list_filter = ("kind",)
    search_fields = ("content__id", "title", "body")
    list_select_related = ("content", "created_by", "category", "genre")
    readonly_fields = ("content", "version", "category", "genre", "kind", "title", "body", "reference_answer", "sort_order", "created_at", "created_by")
    fields = readonly_fields
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
