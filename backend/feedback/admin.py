from django.contrib import admin
from django.utils import timezone
from unfold.admin import ModelAdmin

from .models import Feedback


@admin.register(Feedback)
class FeedbackAdmin(ModelAdmin):
    list_display = (
        "id", "feedback_type", "content_excerpt", "status", "user", "contact", "created_at", "handled_by", "handled_at",
    )
    list_filter = ("feedback_type", "status")
    search_fields = ("content", "contact", "user__username", "admin_note")
    list_select_related = ("user", "handled_by")
    readonly_fields = ("user", "feedback_type", "content", "contact", "created_at", "updated_at", "handled_by", "handled_at")
    fields = (
        "user", "feedback_type", "content", "contact", "status", "admin_note",
        "handled_by", "handled_at", "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("feedback.view_feedback") or request.user.has_perm("feedback.process_feedback")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("feedback.process_feedback")

    def get_readonly_fields(self, request, obj=None):
        if request.user.has_perm("feedback.process_feedback"):
            return self.readonly_fields
        return (*self.readonly_fields, "status", "admin_note")

    @admin.display(description="问题描述")
    def content_excerpt(self, obj):
        return obj.content[:80]

    def save_model(self, request, obj, form, change):
        if change and form.changed_data:
            obj.handled_by = request.user
            obj.handled_at = timezone.now()
        super().save_model(request, obj, form, change)
