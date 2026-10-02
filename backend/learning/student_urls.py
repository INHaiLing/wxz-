from django.urls import path

from .student_api import FavoritesView, PreferenceView, QuestionStateView, StatisticsView

urlpatterns = [
    path("me/questions/<slug:pk>/state/", QuestionStateView.as_view(), name="student-question-state"),
    path("me/favorites/", FavoritesView.as_view(), name="student-favorites"),
    path("me/statistics/", StatisticsView.as_view(), name="student-statistics"),
    path("me/preferences/", PreferenceView.as_view(), name="student-preferences"),
]
