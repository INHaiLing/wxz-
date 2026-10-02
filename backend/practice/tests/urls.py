from django.urls import include, path

from config.urls import urlpatterns as base_patterns

urlpatterns = [path("api/student/v1/", include("practice.student_urls")), *base_patterns]
