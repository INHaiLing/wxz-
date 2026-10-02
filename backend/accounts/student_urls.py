from django.urls import path

from .student_api import LogoutView, WeChatLoginView

urlpatterns = [
    path("auth/wechat/", WeChatLoginView.as_view(), name="student-wechat-login"),
    path("auth/logout/", LogoutView.as_view(), name="student-logout"),
]
