from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import PracticeRound, ResumePosition, RoundItem


class ReadOnlyPracticeAdmin(ModelAdmin):
    actions = None

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PracticeRound)
class PracticeRoundAdmin(ReadOnlyPracticeAdmin):
    list_display = ("id", "user", "source", "mode", "group_size", "item_count", "created_at")
    list_filter = ("source", "mode")


@admin.register(RoundItem)
class RoundItemAdmin(ReadOnlyPracticeAdmin):
    list_display = ("round", "position", "question", "revision")
    list_select_related = ("round", "question", "revision")


@admin.register(ResumePosition)
class ResumePositionAdmin(ReadOnlyPracticeAdmin):
    list_display = ("user", "source", "question", "round", "group_index", "mode", "version", "updated_at")
    list_select_related = ("user", "question", "round")
