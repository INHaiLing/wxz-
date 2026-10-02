from django.conf import settings
from django.views.decorators.debug import sensitive_variables
from rest_framework import serializers
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from common.api import StudentAPIView
from common.errors import BusinessError
from common.limits import check_rate, client_ip
from .services import redeem_code


class RedeemInput(serializers.Serializer):
    code = serializers.CharField(max_length=128, trim_whitespace=False)

    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) != {"code"}:
            raise serializers.ValidationError({"fields": "只允许提供激活码 code。"})
        return super().to_internal_value(data)


class RedeemView(StudentAPIView):
    parser_classes = (JSONParser,)

    @sensitive_variables("serializer")
    def post(self, request):
        limit = getattr(settings, "ACTIVATION_REDEEM_RATE_LIMIT", 10)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise BusinessError("CONFIGURATION_ERROR", "兑换配置不可用。", 503)
        # Each rate transaction commits before the redeem transaction, including
        # malformed bodies, missing keys and rejected business attempts.
        check_rate("activation-user", request.user.pk, limit, seconds=60)
        check_rate("activation-ip", client_ip(request), max(limit * 3, 30), seconds=60)
        serializer = RedeemInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = redeem_code(request.user, serializer.validated_data["code"], request.headers.get("Idempotency-Key"))
        return Response(result, headers={"Cache-Control": "no-store, private"})
