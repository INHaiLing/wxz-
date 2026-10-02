from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin, GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from django.contrib.admin.models import LogEntry
from django.utils import timezone
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import StudentAccount, StudentSession, User, WeChatIdentity


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


@admin.register(StudentAccount)
class StudentAccountAdmin(ReadonlyIdentityAdmin):
    list_display = ('id','username','is_active','date_joined','status_links')
    readonly_fields = list_display
    fields = list_display
    list_filter = ('is_active',)
    search_fields = ('username',)

    def get_queryset(self,request):
        return super().get_queryset(request).filter(is_staff=False,is_superuser=False,wechat_identities__isnull=False).distinct()

    def has_view_permission(self,request,obj=None):
        return request.user.has_perm('accounts.view_studentaccount') or request.user.has_perm('accounts.set_student_status')

    @admin.display(description='启停操作')
    def status_links(self,obj):
        from django.urls import reverse
        from django.utils.html import format_html
        action='disable' if obj.is_active else 'enable'
        return format_html('<a href="{}">{}</a>',reverse('admin:student_status',args=(obj.pk,action)), '停用学员' if obj.is_active else '启用学员')

    def get_urls(self):
        from django.urls import path
        return [path('<int:user_id>/status/<str:action>/',self.admin_site.admin_view(self.status_view),name='student_status')]+super().get_urls()

    def status_view(self,request,user_id,action):
        from django.core import signing
        from django.core.exceptions import PermissionDenied
        from django.http import HttpResponseRedirect
        from django.shortcuts import get_object_or_404
        from django.template.response import TemplateResponse
        from django.urls import reverse
        from common.errors import BusinessError
        from .student_admin_services import set_student_status,student_status_fingerprint
        if not request.user.has_perm('accounts.set_student_status'):
            raise PermissionDenied
        if action not in ('enable','disable'):
            raise PermissionDenied
        user=get_object_or_404(self.get_queryset(request),pk=user_id)
        salt='accounts.student-status.v1'
        fingerprint=student_status_fingerprint(user)
        expected={'actor':request.user.pk,'user':user_id,'action':action,'fingerprint':fingerprint}
        error_message=''; status=200
        if request.method=='POST':
            try:
                if set(request.POST)-{'csrfmiddlewaretoken','confirmation_token','reason','confirm'}:
                    raise BusinessError('INVALID_STATUS_REQUEST','请求字段无效。')
                payload=signing.loads(request.POST.get('confirmation_token',''),salt=salt,max_age=1800)
                if payload!=expected or request.POST.get('confirm')!='yes':
                    raise BusinessError('VERSION_CONFLICT','确认凭证不匹配，请重新预览。',409)
                set_student_status(user_id,request.user,enabled=action=='enable',reason=request.POST.get('reason',''),expected_fingerprint=payload['fingerprint'])
                return HttpResponseRedirect(reverse('admin:accounts_studentaccount_changelist'))
            except signing.BadSignature:
                error_message='确认凭证已过期或无效，请重新预览。'; status=409
            except BusinessError as error:
                error_message=str(error.detail['error']['message']); status=error.status_code
        elif request.method!='GET':
            raise PermissionDenied
        return TemplateResponse(request,'admin/accounts/student_status.html',{
            **self.admin_site.each_context(request),'title':'学员启停确认','opts':self.model._meta,
            'target_user':user,'action':action,'confirmation_token':signing.dumps(expected,salt=salt),
            'error_message':error_message,'active_session_count':user.student_sessions.filter(revoked_at__isnull=True,expires_at__gt=timezone.now()).count(),
        },status=status)
