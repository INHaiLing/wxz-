"""All publication changes pass through these permission checked transactions."""

import json

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Article, Category, Question, QuestionRevision


def require_publish_permission(actor):
    if not (
        actor
        and actor.is_authenticated
        and actor.is_active
        and actor.is_staff
        and actor.has_perm("content.publish_question")
    ):
        raise PermissionDenied("当前账户没有发布或下架题目的权限。")


def _log_publication(question, actor, description):
    LogEntry.objects.create(
        user_id=actor.pk,
        content_type=ContentType.objects.get_for_model(Question),
        object_id=str(question.pk),
        object_repr=str(question)[:200],
        action_flag=CHANGE,
        change_message=json.dumps([{"changed": {"fields": [description]}}], ensure_ascii=False),
    )


@transaction.atomic
def publish_questions(queryset, actor):
    """Publish valid drafts atomically; return the number of changed questions."""
    require_publish_permission(actor)
    # Admin actions carry nullable select_related joins. Lock only the question
    # rows; PostgreSQL rejects FOR UPDATE on the nullable side of an outer join.
    questions = list(queryset.select_related(None).select_for_update().order_by("pk"))
    category_ids = {question.category_id for question in questions if question.category_id}
    article_ids = {question.article_id for question in questions if question.article_id}
    categories = {
        category.pk: category
        for category in Category.objects.select_for_update().filter(pk__in=category_ids).order_by("pk")
    }
    articles = {
        article.pk: article
        for article in Article.objects.select_for_update().filter(pk__in=article_ids).order_by("pk")
    }
    errors = {}
    for question in questions:
        try:
            question.full_clean()
            if question.category_id and not categories[question.category_id].is_active:
                raise ValidationError("关联分类已停用，不能发布。")
            if question.article_id and not articles[question.article_id].is_active:
                raise ValidationError("关联篇目已停用，不能发布。")
        except ValidationError as error:
            errors[question.pk] = error.messages
    if errors:
        raise ValidationError(errors)

    changed = 0
    for question in questions:
        existing = question.published_revision
        same_content = existing is not None and existing.content_payload() == question.content_payload()
        if question.is_published and same_content:
            continue
        if same_content:
            revision = existing
            description = f"恢复发布 v{revision.version}"
        else:
            highest_version = question.revisions.aggregate(highest=Max("version"))["highest"] or 0
            revision = QuestionRevision.objects.create(
                question=question,
                version=highest_version + 1,
                created_by=actor,
                **question.content_payload(),
            )
            description = f"发布 v{revision.version}"
        question.published_revision = revision
        question.is_published = True
        question.save(update_fields=("published_revision", "is_published", "updated_at"))
        _log_publication(question, actor, description)
        changed += 1
    return changed


@transaction.atomic
def unpublish_questions(queryset, actor):
    """Withdraw current access without deleting a draft or its revision history."""
    require_publish_permission(actor)
    changed = 0
    for question in queryset.select_related(None).select_for_update().order_by("pk"):
        if not question.is_published:
            continue
        # A malformed draft must never prevent withdrawing a published version.
        Question.objects.filter(pk=question.pk).update(is_published=False, updated_at=timezone.now())
        question.is_published = False
        _log_publication(question, actor, "下架题目（保留发布修订）")
        changed += 1
    return changed
