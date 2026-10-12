from django.urls import path

from .student_api import WritingDetailView, WritingListView


urlpatterns = [
    path("writings/", WritingListView.as_view(), name="student-writing-list"),
    path("writings/<slug:pk>/", WritingDetailView.as_view(), name="student-writing-detail"),
]
