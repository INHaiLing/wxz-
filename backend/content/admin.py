import re

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.utils import quote, unquote
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from import_export.admin import ImportExportModelAdmin
from unfold.admin import ModelAdmin

from .forms import QuestionAdminForm
from .importing import QuestionImportMixin
from .models import Article, Category, LearningConfiguration, Question, QuestionRevision
from .resources import QuestionResource
from .services import (
    ContentConflict, make_publication_token, publish_questions,
    require_publish_permission, save_question_draft, unpublish_questions,
)


class RetainedContentAdmin(ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        return fields + (("id",) if obj else ())


@admin.register(Category)
class CategoryAdmin(RetainedContentAdmin):
    list_display = ("id", "name", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("id", "name")
    fields = ("id", "name", "sort_order", "is_active")
    ordering = ("sort_order", "id")


@admin.register(Article)
class ArticleAdmin(RetainedContentAdmin):
    list_display = ("id", "title", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("id", "title")
    fields = ("id", "title", "sort_order", "is_active")
    ordering = ("sort_order", "id")


def preview_segments(question, show_answers):
    pieces = re.split(r"(\{\{[0-9]+\}\})", question.stem)
    segments = []
    for piece in pieces:
        if not piece:
            continue
        match = re.fullmatch(r"\{\{([0-9]+)\}\}", piece)
        if match:
            index = int(match.group(1))
            text = question.answers[index] if index < len(question.answers) else "答案缺失"
            segments.append({"text": f"（{text}）" if show_answers else "________", "answer": show_answers})
        else:
            segments.append({"text": piece, "answer": False})
    return segments


@admin.register(Question)
class QuestionAdmin(QuestionImportMixin, ImportExportModelAdmin, RetainedContentAdmin):
    form = QuestionAdminForm
    resource_classes = (QuestionResource,)
    list_display = ("id", "short_stem", "source", "type", "publication_status", "sort_order", "preview_link", "updated_at")
    list_filter = ("source", "type", "is_published", "category", "article")
    search_fields = ("id", "stem", "tag")
    list_select_related = ("category", "article", "published_revision")
    readonly_fields = ("publication_status", "published_version", "preview_link", "created_at", "updated_at")
    actions = ("publish_selected", "unpublish_selected")
    fieldsets = (
        ("题目身份与归属", {"fields": ("id", "source", "category", "article", "type", "tag", "sort_order")}),
        ("草稿内容", {"fields": ("stem", "answers", "draft_token"), "description": "保存只更新草稿；发布后只读接口才能读取新内容。编辑凭证 30 分钟有效。"}),
        ("发布信息", {"fields": ("publication_status", "published_version", "preview_link", "created_at", "updated_at")}),
    )

    @admin.display(description="题干")
    def short_stem(self, obj):
        return obj.stem[:70] + ("…" if len(obj.stem) > 70 else "")

    @admin.display(description="发布状态")
    def publication_status(self, obj):
        if not obj or not obj.pk:
            return "未发布"
        if not obj.is_published:
            return "已下架（保留历史）" if obj.published_revision_id else "草稿"
        return "已发布 · 有未发布修改" if obj.has_unpublished_changes else "已发布"

    @admin.display(description="当前/最近发布版本")
    def published_version(self, obj):
        return f"v{obj.published_revision.version}" if obj and obj.published_revision_id else "尚未发布"

    @admin.display(description="预览")
    def preview_link(self, obj):
        if not obj or obj._state.adding:
            return "保存后可预览"
        url = reverse("admin:content_question_preview", args=(quote(obj.pk),))
        return format_html('<a href="{}">查看默写 / 背诵效果</a>', url)

    def has_publish_permission(self, request):
        try:
            require_publish_permission(request.user)
        except PermissionDenied:
            return False
        return True

    def get_form(self, request, obj=None, **kwargs):
        base_form = super().get_form(request, obj, **kwargs)

        class RequestBoundQuestionForm(base_form):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, request=request, **form_kwargs)

        return RequestBoundQuestionForm

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        response = super().changeform_view(request, object_id, form_url, extra_context)
        if getattr(request, "_draft_conflict", None) and isinstance(response, TemplateResponse):
            response.status_code = 409
        return response

    def _changeform_view(self, request, object_id, form_url, extra_context):
        try:
            with transaction.atomic():
                return super()._changeform_view(request, object_id, form_url, extra_context)
        except ValidationError as error:
            # A conflict can occur after form.is_valid(), when save acquires its
            # row lock. The savepoint has rolled back before re-rendering the
            # bound form, so neither partial changes nor a broken transaction
            # are swallowed. Never turn a stale save into 500.
            request._draft_conflict = "；".join(error.messages)
            return super()._changeform_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        if change:
            save_question_draft(obj, request.user, form.cleaned_data.get("draft_token"))
        else:
            super().save_model(request, obj, form, change)

    def get_urls(self):
        return [
            path(
                "<path:object_id>/preview/",
                self.admin_site.admin_view(self.preview_view),
                name="content_question_preview",
            ),
        ] + super().get_urls()

    def preview_view(self, request, object_id):
        question = self.get_object(request, unquote(object_id))
        if question is None:
            raise Http404("题目不存在。")
        if not self.has_view_permission(request, question):
            raise PermissionDenied("没有查看题目的权限。")
        context = {
            **self.admin_site.each_context(request),
            "title": "题目预览",
            "opts": self.model._meta,
            "original": question,
            "question": question,
            "status": self.publication_status(question),
            "writing_segments": preview_segments(question, False),
            "reciting_segments": preview_segments(question, True),
            "change_url": reverse("admin:content_question_change", args=(quote(question.pk),)),
            "media": self.media,
        }
        return TemplateResponse(request, "admin/content/question/preview.html", context)

    def _publication_action(self, request, queryset, *, publish):
        require_publish_permission(request.user)
        action = "publish_selected" if publish else "unpublish_selected"
        questions = list(queryset.order_by("pk"))
        if request.POST.get("confirm_publication") != action:
            return self._publication_response(request, questions, action, publish,
                confirmation_token=make_publication_token(questions, request.user, action))
        service = publish_questions if publish else unpublish_questions
        try:
            if request.POST.get("select_across", "0") != "0":
                raise ContentConflict("确认时不能扩展所选集合，请重新确认本批题目。")
            count = service(queryset, request.user, confirmation_token=request.POST.get("confirmation_token", ""))
        except ContentConflict as error:
            return self._publication_response(request, questions, action, publish,
                confirmation_error="；".join(error.messages), status=409)
        except ValidationError as error:
            details = error.message_dict if hasattr(error, "message_dict") else error.messages
            self.message_user(request, f"整批操作未生效：{details}", level=messages.ERROR)
        else:
            description = "发布" if publish else "下架"
            self.message_user(request, f"已{description} {count} 道题；状态和内容未变化的题目已跳过。", level=messages.SUCCESS)
        return None

    def _publication_response(self, request, questions, action, publish, *, status=200, **extra):
        return TemplateResponse(request, "admin/content/question/confirm_publication.html", {
            **self.admin_site.each_context(request),
            "title": "确认发布题目" if publish else "确认下架题目",
            "opts": self.model._meta, "questions": questions,
            "action_name": action, "checkbox_name": ACTION_CHECKBOX_NAME,
            "publishing": publish, "media": self.media, **extra,
        }, status=status)

    @admin.action(description="发布所选题目（需确认）", permissions=("publish",))
    def publish_selected(self, request, queryset):
        return self._publication_action(request, queryset, publish=True)

    @admin.action(description="下架所选题目（需确认）", permissions=("publish",))
    def unpublish_selected(self, request, queryset):
        return self._publication_action(request, queryset, publish=False)


@admin.register(QuestionRevision)
class QuestionRevisionAdmin(ModelAdmin):
    list_display = ("question", "version", "source", "type", "created_by", "created_at")
    list_filter = ("source", "type")
    search_fields = ("question__id", "stem")
    list_select_related = ("question", "created_by", "category", "article")
    readonly_fields = (
        "question", "version", "source", "category", "article", "type", "tag", "stem", "answers",
        "sort_order", "created_at", "created_by",
    )
    fields = readonly_fields
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LearningConfiguration)
class LearningConfigurationAdmin(ModelAdmin):
    list_display = ("page_size", "daily_target", "exam_date")
    fields = ("page_size", "daily_target", "exam_date")

    def has_add_permission(self, request):
        # Existing import failure pages can render while a transaction is
        # marked for rollback. Fail closed instead of querying that transaction.
        return (
            super().has_add_permission(request)
            and not connection.needs_rollback
            and not LearningConfiguration.objects.exists()
        )

    def has_delete_permission(self, request, obj=None):
        return False
