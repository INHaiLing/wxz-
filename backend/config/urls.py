from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from content.api import healthz
from operations.api import readyz

admin.site.site_title = "语文题库后台"
admin.site.site_header = "青墨 · 题库管理"
admin.site.index_title = "题库工作台"

urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    path("admin/", admin.site.urls),
    path("api/v1/", include("content.urls")),
    path("api/student/v1/", include("accounts.student_urls")),
    path("api/student/v1/", include("entitlements.student_urls")),
    path("api/student/v1/", include("content.student_urls")),
]
