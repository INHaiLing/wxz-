from django.conf import settings
from rest_framework import serializers
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from common.api import PublicStudentAPIView, StudentAPIView
from common.errors import BusinessError
from common.limits import check_rate, client_ip
from .services import issue_student_session, revoke_session
from .wechat import exchange_code


class LoginInput(serializers.Serializer):
    code = serializers.CharField(max_length=256, trim_whitespace=False)

    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - {"code"}:
            raise serializers.ValidationError({"fields": "只允许提供微信登录 code。"})
        return super().to_internal_value(data)


class WeChatLoginView(PublicStudentAPIView):
    parser_classes = (JSONParser,)

    def post(self, request):
        limit = getattr(settings, "WECHAT_LOGIN_RATE_LIMIT", 30)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise BusinessError("STUDENT_CONFIGURATION_ERROR", "微信登录配置不可用，请联系管理员。", 503)
        # Commit the attempt before exchange/identity transactions, including failures.
        check_rate("student-login", client_ip(request), limit, seconds=60)
        serializer = LoginInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        login = exchange_code(serializer.validated_data["code"])
        token, session = issue_student_session(login)
        return Response(
            {"token": token, "expiresAt": session.expires_at.isoformat(), "user": {"id": session.user_id}},
            headers={"Cache-Control": "no-store, private"},
        )


class LogoutView(StudentAPIView):
    def post(self, request):
        revoke_session(request.auth)
        return Response(status=204, headers={"Cache-Control": "no-store, private"})
