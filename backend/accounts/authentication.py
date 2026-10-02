"""Student bearer identity is independent from Django staff sessions."""

import re

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header

from common.errors import BusinessError
from .models import StudentSession
from .services import token_digest


class StudentBearerAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return "Bearer"

    def authenticate(self, request):
        if "HTTP_AUTHORIZATION" not in request.META:
            return None
        try:
            header = get_authorization_header(request)
        except UnicodeError:
            raise BusinessError("INVALID_TOKEN", "学员登录凭证无效，请重新登录。", 401) from None
        parts = header.split()
        if len(parts) != 2 or parts[0].lower() != b"bearer" or not re.fullmatch(rb"[A-Za-z0-9_-]{43}", parts[1]):
            raise BusinessError("INVALID_TOKEN", "学员登录凭证无效，请重新登录。", 401)
        session = StudentSession.objects.select_related("user").filter(
            token_digest=token_digest(parts[1].decode("ascii")),
            revoked_at__isnull=True, expires_at__gt=timezone.now(),
        ).first()
        if session is None or not session.user.is_active or session.user.is_staff or session.user.is_superuser:
            raise BusinessError("INVALID_TOKEN", "学员登录凭证无效，请重新登录。", 401)
        return session.user, session
