from config.settings import *  # noqa: F401,F403

INSTALLED_APPS = [*INSTALLED_APPS, "feedback.apps.FeedbackConfig"]
ROOT_URLCONF = "feedback.tests.urls"
