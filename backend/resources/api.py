from rest_framework import serializers
from rest_framework.response import Response

from common.api import PublicStudentAPIView
from content.serializers import validate_query_params
from .models import GroupResourceConfiguration


class EmptyQuery(serializers.Serializer):
    pass


class GroupResourceConfigurationView(PublicStudentAPIView):
    http_method_names = ("get", "head", "options")

    def get(self, request):
        validate_query_params(request.query_params, EmptyQuery)
        configuration = GroupResourceConfiguration.current()
        if configuration is None or not configuration.is_enabled:
            return Response({"enabled": False})
        qr_url = configuration.group_qr_code.url if configuration.group_qr_code else None
        return Response({
            "enabled": True,
            "groupQrCode": request.build_absolute_uri(qr_url) if qr_url else None,
            "wechatId": configuration.group_wechat_id,
            "claimCode": configuration.claim_code,
            "description": configuration.description,
            "announcement": configuration.announcement,
            "updatedAt": configuration.updated_at.isoformat(),
        })
