from django.urls import include, path

from django.contrib import admin
from django.urls import path


urlpatterns = [
    path("api/student/v1/", include("resources.urls")),
    path("admin/", admin.site.urls),
]
