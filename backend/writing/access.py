"""Published applied-writing content and the shared free-page policy."""

from django.db.models import F, OuterRef, Q, Subquery

from content.models import LearningConfiguration
from content.access import has_full_access

from .models import WritingContent, WritingKind


def published_writings():
    return WritingContent.objects.filter(
        is_published=True,
        published_revision__isnull=False,
        published_revision__content_id=F("pk"),
        published_revision__category__is_active=True,
    ).filter(
        Q(published_revision__genre__isnull=True)
        | Q(published_revision__genre__is_active=True)
    ).select_related("published_revision", "published_revision__category", "published_revision__genre").order_by(
        "published_revision__sort_order", "pk",
    )


def scope_writings(category_id=None, genre_id=None, kind=None):
    queryset = published_writings()
    if category_id:
        queryset = queryset.filter(published_revision__category_id=category_id)
    if genre_id:
        queryset = queryset.filter(published_revision__genre_id=genre_id)
    if kind:
        queryset = queryset.filter(published_revision__kind=kind)
    return queryset


def _free_subquery(page_size):
    return published_writings().filter(
        published_revision__category_id=OuterRef("published_revision__category_id"),
    ).order_by("published_revision__sort_order", "pk").values("pk")[:page_size]


def free_writings(queryset=None, *, page_size=None):
    candidates = published_writings() if queryset is None else queryset
    size = LearningConfiguration.current().page_size if page_size is None else page_size
    return candidates.filter(pk__in=Subquery(_free_subquery(size)))


def accessible_writings(user, queryset=None, *, page_size=None):
    candidates = published_writings() if queryset is None else queryset
    if has_full_access(user):
        return candidates, True
    return free_writings(candidates, page_size=page_size), False


def can_access_writing(user, writing_id):
    candidates = published_writings().filter(pk=writing_id)
    if not candidates.exists():
        return False
    return accessible_writings(user, candidates)[0].exists()
