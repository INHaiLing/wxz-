from django.urls import path

from .student_api import (
    LearningConfigView, StudentArticleListView, StudentCategoryListView,
    StudentQuestionDetailView, StudentQuestionListView,
)


urlpatterns = [
    path("config/", LearningConfigView.as_view(), name="student-config"),
    path("categories/", StudentCategoryListView.as_view(), name="student-categories"),
    path("articles/", StudentArticleListView.as_view(), name="student-articles"),
    path("questions/", StudentQuestionListView.as_view(), name="student-questions"),
    path("questions/<slug:pk>/", StudentQuestionDetailView.as_view(), name="student-question-detail"),
]
