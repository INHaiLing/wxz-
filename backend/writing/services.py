"""Permission-checked transactions for publishing applied-writing content."""

import hashlib
import json

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import WritingCategory, WritingContent, WritingGenre, WritingRevision


CONFIRMATION_SECONDS = 30 * 60
PUBLICATION_SIGNING_SALT = "writing.admin.publication.v1"
DRAFT_SIGNING_SALT = "writing.admin.draft.v1"


class ContentConflict(ValidationError):
    """An expected stale/tampered admin request, displayed as a conflict."""


def _fingerprint(payload):
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()


def publication_fingerprint(content):
    return _fingerprint({
        "id": str(content.pk),
        "draft": content.content_payload(),
        "is_published": content.is_published,
        "published_revision": content.published_revision_id,
        "updated_at": content.updated_at,
    })


def draft_fingerprint(content):
    return _fingerprint({"id": str(content.pk), **content.content_payload()})


def make_draft_token(content, actor):
    return signing.dumps({
        "owner": str(actor.pk), "id": str(content.pk), "fingerprint": draft_fingerprint(content),
    }, salt=DRAFT_SIGNING_SALT, compress=True)


def check_draft_token(token, content, actor):
    try:
        payload = signing.loads(token or "", salt=DRAFT_SIGNING_SALT, max_age=CONFIRMATION_SECONDS)
    except (signing.BadSignature, TypeError, ValueError) as error:
        raise ContentConflict("编辑凭证缺失、无效或已过期，请重新打开应用文核对最新内容。") from error
    expected = {
        "owner": str(actor.pk), "id": str(content.pk), "fingerprint": draft_fingerprint(content),
    }
    if payload != expected:
        raise ContentConflict("应用文已被其他操作修改，请重新打开内容列表核对最新版本。")


@transaction.atomic
def save_writing_draft(content, actor, token):
    if not (
        actor and actor.is_authenticated and actor.is_active and actor.is_staff
        and actor.has_perm("writing.change_writingcontent")
    ):
        raise PermissionDenied("当前账户没有编辑应用文的权限。")
    try:
        locked = WritingContent.objects.select_for_update().get(pk=content.pk)
    except WritingContent.DoesNotExist as error:
        raise ContentConflict("应用文已不可用，请重新打开内容列表。") from error
    check_draft_token(token, locked, actor)
    content.is_published = locked.is_published
    content.published_revision_id = locked.published_revision_id
    content.save()


def make_publication_token(contents, actor, action):
    return signing.dumps({
        "owner": str(actor.pk), "action": action,
        "versions": {str(content.pk): publication_fingerprint(content) for content in contents},
    }, salt=PUBLICATION_SIGNING_SALT, compress=True)


def _check_publication_token(token, contents, actor, action):
    try:
        payload = signing.loads(token or "", salt=PUBLICATION_SIGNING_SALT, max_age=CONFIRMATION_SECONDS)
    except (signing.BadSignature, TypeError, ValueError) as error:
        raise ContentConflict("确认凭证缺失、无效或已过期，请重新确认本批应用文。") from error
    expected = {
        "owner": str(actor.pk), "action": action,
        "versions": {str(content.pk): publication_fingerprint(content) for content in contents},
    }
    if payload != expected:
        raise ContentConflict("应用文内容、发布状态或所选集合已变化，请重新确认本批内容。")


def require_publish_permission(actor):
    if not (
        actor and actor.is_authenticated and actor.is_active and actor.is_staff
        and actor.has_perm("writing.publish_writingcontent")
    ):
        raise PermissionDenied("当前账户没有发布或下架应用文的权限。")


def _log_publication(content, actor, description):
    LogEntry.objects.create(
        user_id=actor.pk,
        content_type=ContentType.objects.get_for_model(WritingContent),
        object_id=str(content.pk),
        object_repr=str(content)[:200],
        action_flag=CHANGE,
        change_message=json.dumps([{"changed": {"fields": [description]}}], ensure_ascii=False),
    )


@transaction.atomic
def publish_writings(queryset, actor, *, confirmation_token=None):
    require_publish_permission(actor)
    contents = list(queryset.select_related(None).select_for_update().order_by("pk"))
    if confirmation_token is not None:
        _check_publication_token(confirmation_token, contents, actor, "publish_selected")

    category_ids = {content.category_id for content in contents}
    genre_ids = {content.genre_id for content in contents if content.genre_id}
    categories = {
        item.pk: item for item in WritingCategory.objects.select_for_update().filter(pk__in=category_ids)
    }
    genres = {
        item.pk: item for item in WritingGenre.objects.select_for_update().filter(pk__in=genre_ids)
    }
    errors = {}
    for content in contents:
        try:
            content.full_clean()
            if not categories.get(content.category_id) or not categories[content.category_id].is_active:
                raise ValidationError("关联分类已停用，不能发布。")
            if content.genre_id and (not genres.get(content.genre_id) or not genres[content.genre_id].is_active):
                raise ValidationError("关联文种已停用，不能发布。")
        except ValidationError as error:
            errors[content.pk] = error.messages
    if errors:
        raise ValidationError(errors)

    changed = 0
    for content in contents:
        existing = content.published_revision
        same_content = existing is not None and existing.content_payload() == content.content_payload()
        if content.is_published and same_content:
            continue
        if same_content:
            revision = existing
            description = f"恢复发布 v{revision.version}"
        else:
            highest_version = content.revisions.aggregate(highest=Max("version"))["highest"] or 0
            revision = WritingRevision.objects.create(
                content=content,
                version=highest_version + 1,
                created_by=actor,
                **content.content_payload(),
            )
            description = f"发布 v{revision.version}"
        content.published_revision = revision
        content.is_published = True
        content.save(update_fields=("published_revision", "is_published", "updated_at"))
        _log_publication(content, actor, description)
        changed += 1
    return changed


@transaction.atomic
def unpublish_writings(queryset, actor, *, confirmation_token=None):
    require_publish_permission(actor)
    contents = list(queryset.select_related(None).select_for_update().order_by("pk"))
    if confirmation_token is not None:
        _check_publication_token(confirmation_token, contents, actor, "unpublish_selected")
    changed = 0
    for content in contents:
        if not content.is_published:
            continue
        WritingContent.objects.filter(pk=content.pk).update(is_published=False, updated_at=timezone.now())
        content.is_published = False
        _log_publication(content, actor, "下架应用文（保留发布修订）")
        changed += 1
    return changed
