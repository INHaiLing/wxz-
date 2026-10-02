"""Disposable test configuration. Never use this module to serve production."""

import os
import secrets

from django.core.exceptions import ImproperlyConfigured

# Set before importing normal settings, so a private .env cannot select a live DB.
os.environ["DJANGO_SECRET_KEY"] = secrets.token_urlsafe(64)
os.environ["DJANGO_DEBUG"] = "false"
os.environ["DB_ENGINE"] = "sqlite"

from .settings import *  # noqa: E402,F403

test_engine = os.environ.get("TEST_DB_ENGINE", "sqlite")
if test_engine == "sqlite":
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("TEST_SQLITE_PATH", ":memory:"),
        "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
    }}
elif test_engine == "postgresql":
    required = ("TEST_DB_NAME", "TEST_DB_USER", "TEST_DB_PASSWORD", "TEST_DB_HOST")
    if any(not os.environ.get(name) for name in required):
        raise ImproperlyConfigured("PostgreSQL tests require explicit TEST_DB_NAME/USER/PASSWORD/HOST.")
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["TEST_DB_NAME"],
        "USER": os.environ["TEST_DB_USER"],
        "PASSWORD": os.environ["TEST_DB_PASSWORD"],
        "HOST": os.environ["TEST_DB_HOST"],
        "PORT": os.environ.get("TEST_DB_PORT", "5432"),
        "CONN_MAX_AGE": 0,
        "OPTIONS": {
            "connect_timeout": 5,
            "options": "-c lock_timeout=5000 -c statement_timeout=8000 -c idle_in_transaction_session_timeout=10000",
        },
        "TEST": {"NAME": "test_" + os.environ["TEST_DB_NAME"]},
    }}
else:
    raise ImproperlyConfigured("TEST_DB_ENGINE must be sqlite or postgresql.")

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
# Tests don't need slow password stretching; production settings are untouched.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
