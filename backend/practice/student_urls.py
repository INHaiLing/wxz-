from django.urls import path

from .student_api import ResumeView, RoundCreateView, RoundGroupView


urlpatterns = [
    path("practice/rounds/", RoundCreateView.as_view(), name="student-practice-create"),
    path("practice/rounds/<uuid:round_id>/groups/<int:group_index>/", RoundGroupView.as_view(), name="student-practice-group"),
    path("me/resume/", ResumeView.as_view(), name="student-resume"),
]
