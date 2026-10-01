"""Public read shapes and explicit query validation for the content API."""

import re

from rest_framework import serializers

from .models import Article, Category, Question


class StrictPositiveIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if not isinstance(data, str) or re.fullmatch(r"[0-9]+", data) is None:
            self.fail("invalid")
        return super().to_internal_value(data)


class PaginationQuerySerializer(serializers.Serializer):
    page = StrictPositiveIntegerField(min_value=1, required=False)
    page_size = StrictPositiveIntegerField(min_value=1, max_value=100, required=False)


class QuestionQuerySerializer(PaginationQuerySerializer):
    source = serializers.ChoiceField(
        choices=("literature", "classical"), required=False
    )
    categoryId = serializers.SlugField(
        max_length=Category._meta.pk.max_length, required=False
    )
    articleId = serializers.SlugField(
        max_length=Article._meta.pk.max_length, required=False
    )
    type = serializers.ChoiceField(
        choices=("fact", "word", "translation"), required=False
    )


def validate_query_params(query_params, serializer_class):
    """Reject misspelled and repeated parameters rather than widening a query."""
    # A QueryDict is interpreted as HTML form input by DRF, where optional
    # empty values can be omitted. A plain dict preserves explicit empty input
    # so a blank filter cannot silently turn into an unfiltered request.
    serializer = serializer_class(data=query_params.dict())
    errors = {}
    for key in query_params:
        if key not in serializer.fields:
            errors[key] = ["不支持的查询参数。"]
        elif len(query_params.getlist(key)) != 1:
            errors[key] = ["同一查询参数只能提供一次。"]
    if errors:
        raise serializers.ValidationError(errors)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class CategorySerializer(serializers.ModelSerializer):
    sortOrder = serializers.IntegerField(source="sort_order", read_only=True)

    class Meta:
        model = Category
        fields = ("id", "name", "sortOrder")
        read_only_fields = fields


class ArticleSerializer(serializers.ModelSerializer):
    sortOrder = serializers.IntegerField(source="sort_order", read_only=True)

    class Meta:
        model = Article
        fields = ("id", "title", "sortOrder")
        read_only_fields = fields


class PublishedQuestionSerializer(serializers.ModelSerializer):
    """Every content value comes from the published snapshot, never the draft."""

    source = serializers.CharField(source="published_revision.source", read_only=True)
    categoryId = serializers.CharField(
        source="published_revision.category_id", allow_null=True, read_only=True
    )
    articleId = serializers.CharField(
        source="published_revision.article_id", allow_null=True, read_only=True
    )
    type = serializers.CharField(source="published_revision.type", read_only=True)
    tag = serializers.CharField(source="published_revision.tag", read_only=True)
    stem = serializers.CharField(source="published_revision.stem", read_only=True)
    answers = serializers.JSONField(source="published_revision.answers", read_only=True)
    sortOrder = serializers.IntegerField(
        source="published_revision.sort_order", read_only=True
    )
    revision = serializers.IntegerField(source="published_revision.version", read_only=True)

    class Meta:
        model = Question
        fields = (
            "id", "source", "categoryId", "articleId", "type", "tag", "stem",
            "answers", "sortOrder", "revision",
        )
        read_only_fields = fields
