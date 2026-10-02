"""Isolated module verification before the parent integrates registration."""
from config.test_settings import *  # noqa: F403

if "practice.apps.PracticeConfig" not in INSTALLED_APPS:  # noqa: F405
    INSTALLED_APPS = [*INSTALLED_APPS, "practice.apps.PracticeConfig"]  # noqa: F405
