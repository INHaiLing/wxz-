from django.db import transaction
from django.utils import timezone

from common.errors import BusinessError
from common.idempotency import lookup, remember
from entitlements.services import lock_user

from .models import Feedback


def feedback_snapshot(feedback):
    return {
        "id": feedback.pk,
        "type": feedback.feedback_type,
        "status": feedback.status,
        "createdAt": feedback.created_at.isoformat(),
    }


@transaction.atomic
def submit_feedback(user, payload, key):
    """Create one private feedback record and make retries return the first result."""
    user = lock_user(user)
    if not user.is_active or user.is_staff or user.is_superuser:
        raise BusinessError("AUTH_REQUIRED", "学员账户已不可用，请重新登录。", 401)
    request_payload = {
        "type": payload["feedback_type"],
        "content": payload["content"],
        "contact": payload.get("contact", ""),
    }
    previous = lookup(user, "feedback.submit", key, request_payload)
    if previous:
        return previous.response
    feedback = Feedback.objects.create(
        user=user,
        feedback_type=payload["feedback_type"],
        content=payload["content"],
        contact=payload.get("contact", ""),
    )
    response = feedback_snapshot(feedback)
    remember(user, "feedback.submit", key, request_payload, response)
    return response
