from django.contrib import admin, messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from common.errors import BusinessError
from unfold.admin import ModelAdmin
from .models import Order, PaymentTask, PaymentEvent
from .tasks import reschedule_task, task_fingerprint


class ReadOnlyAdmin(ModelAdmin):
    def get_readonly_fields(self,request,obj=None): return tuple(f.name for f in self.model._meta.fields)
    def has_add_permission(self,request): return False
    def has_change_permission(self,request,obj=None): return False
    def has_delete_permission(self,request,obj=None): return False


@admin.register(Order)
class OrderAdmin(ReadOnlyAdmin):
    list_display=('id','user','price_fen','channel','status','created_at')
    list_filter=('status','channel','environment')
    search_fields=('id','user__username','platform_order_id')
    fields=('id','user','product','product_name','scope','price_fen','platform_product_id','environment','channel',
            'status','prepared_at','platform_order_id','transaction_id','last_platform_status','paid_at','fulfilled_at','refunded_at','review_reason','created_at','updated_at')


@admin.register(PaymentTask)
class PaymentTaskAdmin(ReadOnlyAdmin):
    list_display=('order','kind','status','attempts','next_run_at','last_error_code','retry_link')
    list_filter=('kind','status')
    fields=('order','kind','status','attempts','next_run_at','lease_until','last_error_code','created_at','updated_at','retry_link')

    def get_readonly_fields(self, request, obj=None):
        return self.fields

    def get_urls(self):
        return [path('<int:task_id>/reschedule/', self.admin_site.admin_view(self.reschedule_view), name='payments_task_reschedule')] + super().get_urls()

    @admin.display(description='核查后重调度')
    def retry_link(self, obj):
        if obj.status == 'failed':
            return format_html('<a href="{}">预览并重调度失败任务</a>', reverse('admin:payments_task_reschedule', args=(obj.pk,)))
        return '当前任务无人工重调度入口'

    def reschedule_view(self, request, task_id):
        task = get_object_or_404(PaymentTask, pk=task_id)
        if not self.has_view_permission(request, task) or not request.user.has_perm('payments.reschedule_paymenttask'):
            raise PermissionDenied('没有支付核查重调度权限。')
        salt = 'payments.task.reschedule.v1'
        context = {**self.admin_site.each_context(request), 'opts': self.model._meta, 'media': self.media,
                   'title': '确认核查并重调度失败任务', 'task': task,
                   'back_url': reverse('admin:payments_paymenttask_change', args=(task.pk,))}
        if request.method == 'POST':
            try:
                try:
                    value = signing.loads(request.POST.get('confirmation_token', ''), salt=salt, max_age=1800)
                except (signing.BadSignature, ValueError, TypeError):
                    raise BusinessError('VERSION_CONFLICT', '确认凭证无效或已过期，请重新预览。', 409) from None
                if not isinstance(value, dict) or value.get('actor') != request.user.pk or value.get('id') != task.pk or value.get('action') != 'reschedule' or not isinstance(value.get('fingerprint'), str):
                    raise BusinessError('VERSION_CONFLICT', '确认凭证与操作不匹配。', 409)
                if request.POST.get('confirm') != 'yes':
                    raise BusinessError('CONFIRMATION_REQUIRED', '请先确认核查结果。')
                reschedule_task(task.pk, request.user, request.POST.get('reason', ''), value['fingerprint'])
            except BusinessError as error:
                context['error'] = str(error.detail['error']['message'])
                return TemplateResponse(request, 'admin/payments/reschedule.html', context, status=error.status_code)
            self.message_user(request, '任务已重新排队，订单状态不会被手工修改。', messages.SUCCESS)
            return HttpResponseRedirect(context['back_url'])
        context['confirmation_token'] = signing.dumps({'actor': request.user.pk, 'id': task.pk, 'action': 'reschedule', 'fingerprint': task_fingerprint(task)}, salt=salt)
        return TemplateResponse(request, 'admin/payments/reschedule.html', context)


@admin.register(PaymentEvent)
class PaymentEventAdmin(ReadOnlyAdmin):
    list_display = ('key', 'order', 'kind', 'outcome', 'error_code', 'created_at')
    list_filter = ('kind', 'outcome')
    search_fields = ('order__id', 'external_id')
