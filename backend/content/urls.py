from django.urls import path

from .api import ArticleListView, CategoryListView, QuestionDetailView, QuestionListView


app_name = "content"

urlpatterns = [
    path("categories/", CategoryListView.as_view(), name="category-list"),
    path("articles/", ArticleListView.as_view(), name="article-list"),
    path("questions/", QuestionListView.as_view(), name="question-list"),
    path("questions/<slug:pk>/", QuestionDetailView.as_view(), name="question-detail"),
]
