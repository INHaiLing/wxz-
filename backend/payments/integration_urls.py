from django.urls import path
from .webhook import virtual_payment

urlpatterns = [path("wechat/virtual-payment/", virtual_payment, name="wechat-virtual-payment")]
