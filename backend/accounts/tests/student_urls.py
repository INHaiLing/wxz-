"""Module-only URL configuration; never mounted by production settings."""

from django.urls import include, path
from rest_framework.response import Response

from common.api import PublicStudentAPIView, StudentAPIView
from config.urls import urlpatterns as existing_urls


class PublicProbe(PublicStudentAPIView):
    def get(self, request):
        return Response({"anonymous": not request.user.is_authenticated})


class PrivateProbe(StudentAPIView):
    def get(self, request):
        return Response({"userId": request.user.pk})


urlpatterns = existing_urls + [
    path("api/student/v1/", include("accounts.student_urls")),
    path("api/student/v1/probe/public/", PublicProbe.as_view()),
    path("api/student/v1/probe/private/", PrivateProbe.as_view()),
]
