from django.urls import include, path
from config.urls import urlpatterns as existing_urls

urlpatterns = existing_urls + [path("api/student/v1/", include("learning.student_urls"))]
