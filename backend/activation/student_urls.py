from django.urls import path

from .api import RedeemView


urlpatterns = [path("activation/redeem/", RedeemView.as_view(), name="student-activation-redeem")]
