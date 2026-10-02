from django.urls import path

from .api import MyEntitlementsView, ProductListView


urlpatterns = [
    path("products/", ProductListView.as_view(), name="student-products"),
    path("me/entitlements/", MyEntitlementsView.as_view(), name="student-entitlements"),
]
