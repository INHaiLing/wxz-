from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from common.errors import BusinessError
from common.idempotency import payload_digest
from entitlements.services import _audit, lock_user, _require_admin
from .models import StudentSession


def student_status_fingerprint(user):
    return payload_digest({"id":user.pk,"active":user.is_active,"staff":user.is_staff,
        "superuser":user.is_superuser,"identities":list(user.wechat_identities.order_by('id').values_list('id',flat=True))})


@transaction.atomic
def set_student_status(user_id, actor, *, enabled, reason, expected_fingerprint):
    actor=get_user_model().objects.get(pk=actor.pk)
    _require_admin(actor,'accounts.set_student_status')
    if type(enabled) is not bool or not isinstance(reason,str) or not 1<=len(reason.strip())<=500:
        raise BusinessError('INVALID_STATUS_REQUEST','请选择启停状态并填写 1～500 字理由。')
    user=lock_user(user_id)
    if user.is_staff or user.is_superuser or not user.wechat_identities.exists():
        raise BusinessError('FORBIDDEN','只能启停已关联微信的学员，不能修改管理员。',403)
    if expected_fingerprint!=student_status_fingerprint(user):
        raise BusinessError('VERSION_CONFLICT','学员状态已变化，请重新预览确认。',409)
    if user.is_active==enabled:
        return user
    user.is_active=enabled
    user.save(update_fields=('is_active',))
    if not enabled:
        StudentSession.objects.filter(user=user,revoked_at__isnull=True).update(revoked_at=timezone.now())
    _audit('student_status_changed',user=user,actor=actor,enabled=enabled,reason=reason.strip())
    return user
