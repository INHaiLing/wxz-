from rest_framework import serializers

from .models import FeedbackType


class StrictInput(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError({"fields": "不支持的请求字段。"})
        return super().to_internal_value(data)


class FeedbackInput(StrictInput):
    type = serializers.ChoiceField(source="feedback_type", choices=FeedbackType.choices)
    content = serializers.CharField(max_length=5000, trim_whitespace=True)
    contact = serializers.CharField(max_length=200, required=False, allow_blank=True, trim_whitespace=True)

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("问题描述不能为空。")
        return value
