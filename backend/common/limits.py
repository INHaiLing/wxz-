import hashlib
import hmac
import ipaddress
import time
from datetime import datetime, timezone

from django.conf import settings
from django.db import transaction
from django.db.models import F

from .errors import BusinessError
from .models import RateBucket


def client_ip(request):
    peer = request.META.get("REMOTE_ADDR", "unknown")
    if peer in getattr(settings, "TRUSTED_PROXY_IPS", ()):
        forwarded = request.META.get("HTTP_X_REAL_IP", "")
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return peer


@transaction.atomic
def check_rate(scope, subject, limit, seconds=60):
    """Shared database limits; never trust unconfigured proxy X-Forwarded-For."""
    digest = hmac.new(settings.SECRET_KEY.encode(), str(subject).encode(), hashlib.sha256).hexdigest()
    start = datetime.fromtimestamp(int(time.time()) // seconds * seconds, timezone.utc)
    bucket, _ = RateBucket.objects.get_or_create(scope=scope, subject_digest=digest, window_start=start)
    if not RateBucket.objects.filter(pk=bucket.pk, count__lt=limit).update(count=F("count") + 1):
        raise BusinessError("RATE_LIMITED", "操作过于频繁，请稍后再试。", 429)
