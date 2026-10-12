from rest_framework import serializers

from content.serializers import StrictPositiveIntegerField

from .models import WritingKind


class WritingQuery(serializers.Serializer):
    page = StrictPositiveIntegerField(min_value=1, max_value=1000000, required=False)
    categoryId = serializers.SlugField(max_length=64, required=False)
    genreId = serializers.SlugField(max_length=64, required=False)
    kind = serializers.ChoiceField(choices=WritingKind.choices, required=False)


def serialize_writing(writing):
    revision = writing.published_revision
    return {
        "id": writing.pk,
        "categoryId": revision.category_id,
        "categoryName": revision.category.name,
        "genreId": revision.genre_id,
        "genreName": revision.genre.name if revision.genre_id else None,
        "kind": revision.kind,
        "title": revision.title,
        "body": revision.body,
        "referenceAnswer": revision.reference_answer if revision.kind == WritingKind.EXERCISE else "",
        "sortOrder": revision.sort_order,
        "version": revision.version,
        "publishedAt": revision.created_at.isoformat(),
    }
