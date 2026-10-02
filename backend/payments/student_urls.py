from django.urls import path
from .api import OrderDetailView, OrderListView, PaymentPrepareView

urlpatterns=[
    path('orders/',OrderListView.as_view(),name='student-orders'),
    path('orders/<str:order_id>/',OrderDetailView.as_view(),name='student-order-detail'),
    path('orders/<str:order_id>/payment/',PaymentPrepareView.as_view(),name='student-payment-prepare'),
]
