from django.urls import path

from .api import FeedbackCreateView


urlpatterns = [
    path("feedback/", FeedbackCreateView.as_view(), name="student-feedback-create"),
]
