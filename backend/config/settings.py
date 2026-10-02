"""Local-first settings; deployment requires a separate production review."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.environ.get(name, str(default)).lower()
    if value not in {"1", "0", "true", "false"}:
        raise ImproperlyConfigured(f"{name} must be true or false.")
    return value in {"1", "true"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if len(SECRET_KEY) < 32 or SECRET_KEY.startswith("replace-"):
    raise ImproperlyConfigured("请先运行 backend/setup.ps1，生成本机 .env 和随机密钥。")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = [
    host.strip() for host in os.environ.get(
        "DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]"
    ).split(",") if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
TRUSTED_PROXY_IPS = [ip.strip() for ip in os.environ.get("TRUSTED_PROXY_IPS", "").split(",") if ip.strip()]

# Student and channel capabilities are explicitly disabled until configured.
WECHAT_APP_ID = os.environ.get("WECHAT_APP_ID", "")
WECHAT_APP_SECRET = os.environ.get("WECHAT_APP_SECRET", "")
STUDENT_SESSION_ENCRYPTION_KEYS = tuple(
    key.strip() for key in os.environ.get("STUDENT_SESSION_ENCRYPTION_KEYS", "").split(",") if key.strip()
)
WECHAT_LOGIN_TIMEOUT_SECONDS = 5
WECHAT_LOGIN_RATE_LIMIT = 30
VIRTUAL_PAYMENT_ENABLED = env_bool("VIRTUAL_PAYMENT_ENABLED", False)
VIRTUAL_PAYMENT_ANDROID_ENABLED = env_bool("VIRTUAL_PAYMENT_ANDROID_ENABLED", True)
VIRTUAL_PAYMENT_IOS_ENABLED = env_bool("VIRTUAL_PAYMENT_IOS_ENABLED", False)
VIRTUAL_PAYMENT_ENV = int(os.environ.get("VIRTUAL_PAYMENT_ENV", "0"))
VIRTUAL_PAYMENT_OFFER_ID = os.environ.get("VIRTUAL_PAYMENT_OFFER_ID", "")
VIRTUAL_PAYMENT_APP_KEY = os.environ.get("VIRTUAL_PAYMENT_APP_KEY", "")
VIRTUAL_PAYMENT_SANDBOX_APP_KEY = os.environ.get("VIRTUAL_PAYMENT_SANDBOX_APP_KEY", "")
VIRTUAL_PAYMENT_CALLBACK_TOKEN = os.environ.get("VIRTUAL_PAYMENT_CALLBACK_TOKEN", "")
VIRTUAL_PAYMENT_CALLBACK_AES_KEY = os.environ.get("VIRTUAL_PAYMENT_CALLBACK_AES_KEY", "")
ACTIVATION_REDEEM_RATE_LIMIT = 10

INSTALLED_APPS = [
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.import_export",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "import_export",
    "common.apps.CommonConfig",
    "accounts.apps.AccountsConfig",
    "content.apps.ContentConfig",
    "entitlements.apps.EntitlementsConfig",
    "payments.apps.PaymentsConfig",
    "learning.apps.LearningConfig",
    "activation.apps.ActivationConfig",
    "practice.apps.PracticeConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DB_ENGINE = os.environ.get("DB_ENGINE", "sqlite")
if DB_ENGINE == "sqlite":
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / os.environ.get("SQLITE_PATH", "db.sqlite3"),
        "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
    }}
elif DB_ENGINE == "postgresql":
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "chinese_study"),
        "USER": os.environ.get("DB_USER", "chinese_study"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }}
else:
    raise ImproperlyConfigured("DB_ENGINE must be sqlite or postgresql.")

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / ".local" / "media"
MEDIA_URL = "/media/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
LOGIN_URL = "/admin/login/"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAdminUser"],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}
IMPORT_EXPORT_USE_TRANSACTIONS = True
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

UNFOLD = {
    "SITE_TITLE": "语文题库后台",
    "SITE_HEADER": "青墨 · 题库管理",
    "SITE_SUBHEADER": "专升本语文学习",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "BORDER_RADIUS": "8px",
    "COLORS": {"primary": {
        "50": "rgb(240 253 250)", "100": "rgb(204 251 241)", "200": "rgb(153 246 228)",
        "300": "rgb(94 234 212)", "400": "rgb(45 212 191)", "500": "rgb(20 184 166)",
        "600": "rgb(13 148 136)", "700": "rgb(15 118 110)", "800": "rgb(17 94 89)",
        "900": "rgb(19 78 74)", "950": "rgb(4 47 46)",
    }},
}
