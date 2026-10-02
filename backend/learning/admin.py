from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import LearningActivity, LearningPreference, QuestionState


class ReadonlyLearningAdmin(ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QuestionState)
class QuestionStateAdmin(ReadonlyLearningAdmin):
    list_display = ("user", "question_id", "favorite", "mastered", "version", "updated_at")
    list_filter = ("favorite", "mastered")
    readonly_fields = ("user", "question_id", "favorite", "mastered", "version", "updated_at")
    fields = readonly_fields


@admin.register(LearningActivity)
class LearningActivityAdmin(ReadonlyLearningAdmin):
    list_display = ("user", "question_id", "study_date", "created_at")
    date_hierarchy = "study_date"
    readonly_fields = ("user", "question_id", "study_date", "created_at")
    fields = readonly_fields


@admin.register(LearningPreference)
class LearningPreferenceAdmin(ReadonlyLearningAdmin):
    list_display = ("user", "mode", "daily_target", "version", "updated_at")
    readonly_fields = ("user", "mode", "daily_target", "version", "updated_at")
    fields = readonly_fields
