"""Student login creates identities atomically and retains only token digests."""

import hashlib
import secrets
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from common.errors import BusinessError
from .crypto import decrypt_session_key, encrypt_session_key
from .models import StudentSession, User, WeChatIdentity


def token_digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@transaction.atomic
def issue_student_session(login):
    ciphertext = encrypt_session_key(login.session_key)
    identity = WeChatIdentity.objects.filter(app_id=login.app_id, openid=login.openid).first()
    if identity is None:
        try:
            # A losing unique-identity insert rolls back its candidate User too.
            with transaction.atomic():
                user = User.objects.create_user(username="wx_" + uuid.uuid4().hex, password=None)
                identity = WeChatIdentity.objects.create(
                    user=user, app_id=login.app_id, openid=login.openid,
                    encrypted_session_key=ciphertext,
                )
        except IntegrityError:
            identity = WeChatIdentity.objects.filter(app_id=login.app_id, openid=login.openid).first()
            if identity is None:
                raise
    user = User.objects.select_for_update().get(pk=identity.user_id)
    identity = WeChatIdentity.objects.select_for_update().get(pk=identity.pk)
    if not user.is_active or user.is_staff or user.is_superuser:
        raise BusinessError("STUDENT_DISABLED", "当前学员账号不可用，请联系管理员。", 403)
    identity.encrypted_session_key = ciphertext
    identity.save(update_fields=("encrypted_session_key", "updated_at"))
    raw_token = secrets.token_urlsafe(32)
    session = StudentSession.objects.create(
        user=user, token_digest=token_digest(raw_token), encrypted_session_key=ciphertext,
        expires_at=timezone.now() + timedelta(days=7),
    )
    return raw_token, session


def decrypt_wechat_session_key(identity_or_session):
    return decrypt_session_key(identity_or_session.encrypted_session_key)


@transaction.atomic
def revoke_session(session):
    User.objects.select_for_update().get(pk=session.user_id)
    StudentSession.objects.filter(pk=session.pk, revoked_at__isnull=True).update(revoked_at=timezone.now())
