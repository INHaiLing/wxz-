from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import GroupResourceConfiguration


@admin.register(GroupResourceConfiguration)
class GroupResourceConfigurationAdmin(ModelAdmin):
    list_display = ("is_enabled", "group_wechat_id", "updated_by", "updated_at")
    fields = (
        "is_enabled", "group_qr_code", "group_wechat_id", "claim_code",
        "description", "announcement", "updated_by", "updated_at",
    )
    readonly_fields = ("updated_by", "updated_at")

    def has_add_permission(self, request):
        return (
            request.user.has_perm("resources.change_groupresourceconfiguration")
            and not GroupResourceConfiguration.objects.exists()
        )

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        if not request.user.has_perm("resources.change_groupresourceconfiguration"):
            return tuple(field.name for field in self.model._meta.fields)
        return fields

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
