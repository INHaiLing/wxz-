from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import StudentSession, User


@receiver(post_save, sender=User)
def revoke_disabled_student_sessions(sender, instance, **kwargs):
    if not instance.is_active:
        StudentSession.objects.filter(user_id=instance.pk, revoked_at__isnull=True).update(revoked_at=timezone.now())
