"""Disposable module verification settings, not a runtime feature flag."""

from config.test_settings import *  # noqa: F403,F401

if "learning.apps.LearningConfig" not in INSTALLED_APPS:
    INSTALLED_APPS = [*INSTALLED_APPS, "learning.apps.LearningConfig"]
