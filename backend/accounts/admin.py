from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin, GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from django.contrib.admin.models import LogEntry
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import StudentSession, User, WeChatIdentity


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


@admin.register(LogEntry)
class AuditLogAdmin(ModelAdmin):
    list_display = ("action_time", "user", "content_type", "object_repr", "action_flag", "change_message")
    list_filter = ("action_flag", "content_type")
    search_fields = ("object_repr", "change_message", "user__username")
    date_hierarchy = "action_time"
    readonly_fields = (
        "action_time", "user", "content_type", "object_id", "object_repr", "action_flag", "change_message",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadonlyIdentityAdmin(ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WeChatIdentity)
class WeChatIdentityAdmin(ReadonlyIdentityAdmin):
    list_display = ("id", "user", "app_id", "masked_openid", "updated_at")
    list_select_related = ("user",)
    search_fields = ("user__username",)
    readonly_fields = ("user", "app_id", "masked_openid", "created_at", "updated_at")
    fields = readonly_fields

    @admin.display(description="微信身份（脱敏）")
    def masked_openid(self, obj):
        return obj.openid[:4] + "…" + obj.openid[-4:] if len(obj.openid) > 8 else "已关联"


@admin.register(StudentSession)
class StudentSessionAdmin(ReadonlyIdentityAdmin):
    list_display = ("id", "user", "created_at", "expires_at", "revoked_at")
    list_select_related = ("user",)
    list_filter = ("revoked_at",)
    readonly_fields = ("user", "created_at", "expires_at", "revoked_at")
    fields = readonly_fields
