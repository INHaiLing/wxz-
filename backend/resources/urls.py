from django.urls import path

from .api import GroupResourceConfigurationView


urlpatterns = [
    path("resources/group/", GroupResourceConfigurationView.as_view(), name="student-group-resources"),
]
