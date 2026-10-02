from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import Order, PaymentTask


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
    list_display=('order','kind','status','attempts','next_run_at','last_error_code')
    list_filter=('kind','status')
    fields=('order','kind','status','attempts','next_run_at','lease_until','last_error_code','created_at','updated_at')
