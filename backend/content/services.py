"""All publication changes pass through these permission checked transactions."""

import hashlib
import json

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Article, Category, Question, QuestionRevision


CONFIRMATION_SECONDS = 30 * 60
DRAFT_SIGNING_SALT = "content.admin.draft.v1"
PUBLICATION_SIGNING_SALT = "content.admin.publication.v1"


class ContentConflict(ValidationError):
    """An expected stale/tampered admin request, displayed as an HTTP 409."""


def _fingerprint(payload):
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()


def draft_fingerprint(question):
    return _fingerprint({"id": str(question.pk), **question.content_payload()})


def publication_fingerprint(question):
    return _fingerprint({
        "draft": draft_fingerprint(question),
        "is_published": question.is_published,
        "published_revision": question.published_revision_id,
        "updated_at": question.updated_at,
    })


def make_draft_token(question, actor):
    return signing.dumps({
        "owner": str(actor.pk), "id": str(question.pk), "fingerprint": draft_fingerprint(question),
    }, salt=DRAFT_SIGNING_SALT, compress=True)


def check_draft_token(token, question, actor):
    try:
        payload = signing.loads(token or "", salt=DRAFT_SIGNING_SALT, max_age=CONFIRMATION_SECONDS)
    except (signing.BadSignature, TypeError, ValueError) as error:
        raise ContentConflict("编辑凭证缺失、无效或已过期，请重新打开题目核对最新内容。") from error
    expected = {
        "owner": str(actor.pk), "id": str(question.pk), "fingerprint": draft_fingerprint(question),
    }
    if payload != expected:
        raise ContentConflict("题目已被其他操作修改，或编辑凭证不匹配，请重新打开题目核对最新内容。")


def make_publication_token(questions, actor, action):
    return signing.dumps({
        "owner": str(actor.pk), "action": action,
        "versions": {str(question.pk): publication_fingerprint(question) for question in questions},
    }, salt=PUBLICATION_SIGNING_SALT, compress=True)


def _check_publication_token(token, questions, actor, action):
    try:
        payload = signing.loads(token or "", salt=PUBLICATION_SIGNING_SALT, max_age=CONFIRMATION_SECONDS)
    except (signing.BadSignature, TypeError, ValueError) as error:
        raise ContentConflict("确认凭证缺失、无效或已过期，请重新确认本批题目。") from error
    expected = {
        "owner": str(actor.pk), "action": action,
        "versions": {str(question.pk): publication_fingerprint(question) for question in questions},
    }
    if payload != expected:
        raise ContentConflict("题目内容、发布状态或所选集合已变化，或确认凭证不匹配，请重新确认本批题目。")


@transaction.atomic
def save_question_draft(question, actor, token):
    if not (
        actor and actor.is_authenticated and actor.is_active and actor.is_staff
        and actor.has_perm("content.change_question")
    ):
        raise PermissionDenied("当前账户没有编辑题目的权限。")
    try:
        locked = Question.objects.select_for_update().get(pk=question.pk)
    except Question.DoesNotExist as error:
        raise ContentConflict("题目已不可用，请重新打开题目列表。") from error
    check_draft_token(token, locked, actor)
    # Publication can change without changing the draft while this form is open.
    question.is_published = locked.is_published
    question.published_revision_id = locked.published_revision_id
    question.save()


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
def publish_questions(queryset, actor, *, confirmation_token=None):
    """Publish valid drafts atomically; return the number of changed questions."""
    require_publish_permission(actor)
    # Admin actions carry nullable select_related joins. Lock only the question
    # rows; PostgreSQL rejects FOR UPDATE on the nullable side of an outer join.
    questions = list(queryset.select_related(None).select_for_update().order_by("pk"))
    if confirmation_token is not None:
        _check_publication_token(confirmation_token, questions, actor, "publish_selected")
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
def unpublish_questions(queryset, actor, *, confirmation_token=None):
    """Withdraw current access without deleting a draft or its revision history."""
    require_publish_permission(actor)
    questions = list(queryset.select_related(None).select_for_update().order_by("pk"))
    if confirmation_token is not None:
        _check_publication_token(confirmation_token, questions, actor, "unpublish_selected")
    changed = 0
    for question in questions:
        if not question.is_published:
            continue
        # A malformed draft must never prevent withdrawing a published version.
        Question.objects.filter(pk=question.pk).update(is_published=False, updated_at=timezone.now())
        question.is_published = False
        _log_publication(question, actor, "下架题目（保留发布修订）")
        changed += 1
    return changed
