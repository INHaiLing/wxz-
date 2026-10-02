"""Linux production profile. Required inputs must be supplied externally."""
from .settings import *  # noqa: F403
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured


if DEBUG or DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured("生产必须关闭 DEBUG 并使用 PostgreSQL。")
if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured("生产 DJANGO_SECRET_KEY 至少 50 位随机字符。")
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS or any(h in ("localhost", "127.0.0.1", "[::1]") for h in ALLOWED_HOSTS):
    raise ImproperlyConfigured("生产须配置明确公网域名，不能使用默认本机 Host。")
if not CSRF_TRUSTED_ORIGINS or any(not o.startswith("https://") or "*" in o for o in CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured("生产 CSRF_TRUSTED_ORIGINS 须为明确 HTTPS 地址。")
if not STUDENT_SESSION_ENCRYPTION_KEYS:
    raise ImproperlyConfigured("生产必须配置并独立备份学员会话加密密钥。")
try:
    for key in STUDENT_SESSION_ENCRYPTION_KEYS:
        Fernet(key)
except (TypeError, ValueError) as error:
    raise ImproperlyConfigured("学员会话加密密钥格式无效。") from error
if not DATABASES["default"].get("PASSWORD"):
    raise ImproperlyConfigured("生产数据库密码不能为空。")

# Nginx exclusively binds the application to loopback and overwrites this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
# These optional domain-wide commitments require ownership/TLS review.
# Keep all other deployment warnings fatal; never opt client subdomains in blindly.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
STATIC_ROOT = Path(os.environ.get("DJANGO_STATIC_ROOT", "/srv/chinese-study/static"))
MEDIA_ROOT = Path(os.environ.get("DJANGO_MEDIA_ROOT", "/var/lib/chinese-study/imports"))
DATABASES["default"]["OPTIONS"] = {"connect_timeout": 10, "options": "-c statement_timeout=120000"}
MIDDLEWARE = ["operations.logging.RequestLoggingMiddleware", *MIDDLEWARE]
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"safe": {"()": "operations.logging.SafeJSONFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "safe"}},
    "loggers": {
        "operations.http": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
