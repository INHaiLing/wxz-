from django.urls import include, path

from config.urls import urlpatterns as original_patterns


urlpatterns = [
    path("api/student/v1/", include("content.student_urls")),
    *original_patterns,
]
