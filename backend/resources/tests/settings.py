from config.test_settings import *  # noqa: F403,F401

if "resources.apps.ResourcesConfig" not in INSTALLED_APPS:
    INSTALLED_APPS = [*INSTALLED_APPS, "resources.apps.ResourcesConfig"]
