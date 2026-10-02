from django.conf import settings
from django.db import models


class IdempotencyRecord(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    operation = models.CharField(max_length=80)
    key = models.CharField(max_length=128)
    request_digest = models.CharField(max_length=64)
    response = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=("user", "operation", "key"), name="common_unique_idempotency",
        )]


class RateBucket(models.Model):
    scope = models.CharField(max_length=64)
    subject_digest = models.CharField(max_length=64)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=("scope", "subject_digest", "window_start"), name="common_unique_rate_bucket",
        )]
