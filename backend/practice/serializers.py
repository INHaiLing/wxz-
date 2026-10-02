from rest_framework import serializers

from content.student_api import StudentQuestionQuery


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({"non_field_errors": ["请提供 JSON 对象。"]})
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: "不支持的字段。" for key in unknown})
        return super().to_internal_value(data)


class StrictSlug(serializers.SlugField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictChoice(serializers.ChoiceField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid_choice", input="非字符串")
        return super().to_internal_value(data)


class StrictInteger(serializers.IntegerField):
    def to_internal_value(self, data):
        if not isinstance(data, int) or isinstance(data, bool):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictUUID(serializers.UUIDField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class RoundRequest(StrictSerializer):
    source = StrictChoice(choices=("literature", "classical"))
    categoryId = StrictSlug(max_length=64, required=False, allow_null=True)
    articleId = StrictSlug(max_length=64, required=False, allow_null=True)
    type = StrictChoice(choices=("fact", "word", "translation"), required=False, allow_null=True)
    mode = StrictChoice(choices=("writing", "reciting"))

    def validate(self, values):
        scope = {key: value for key, value in values.items() if key in ("source", "categoryId", "articleId", "type") and value is not None}
        validator = StudentQuestionQuery(data=scope)
        validator.is_valid(raise_exception=True)
        for key in ("categoryId", "articleId", "type"):
            values.setdefault(key, None)
        return values


class ResumeRequest(RoundRequest):
    questionId = StrictSlug(max_length=64)
    roundId = StrictUUID(required=False, allow_null=True)
    groupIndex = StrictInteger(min_value=1, max_value=10000, required=False, allow_null=True)
    baseVersion = StrictInteger(min_value=0, max_value=2147483646)

    def validate(self, values):
        values = super().validate(values)
        values.setdefault("roundId", None)
        values.setdefault("groupIndex", None)
        if (values["roundId"] is None) != (values["groupIndex"] is None):
            raise serializers.ValidationError({"groupIndex": "随机续学必须同时提供轮次和组号。"})
        if values["roundId"] is not None:
            values["roundId"] = str(values["roundId"])
        return values
