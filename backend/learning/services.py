"""Explicit versioned writes serialized by the authenticated User row."""

from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from common.errors import BusinessError
from common.idempotency import lookup, remember
from content.models import Question, Source
from entitlements.services import lock_user
from .models import LearningActivity, LearningPreference, QuestionState


SHANGHAI = ZoneInfo("Asia/Shanghai")


def shanghai_today():
    return timezone.now().astimezone(SHANGHAI).date()


def state_snapshot(state, question_id):
    return {"questionId": str(question_id), "favorite": state.favorite if state else False,
            "mastered": state.mastered if state else False, "version": state.version if state else 0}


def preference_snapshot(user):
    from content.models import LearningConfiguration
    preference = LearningPreference.objects.filter(user=user).first()
    return {"mode": preference.mode if preference else "writing",
            "dailyTarget": preference.daily_target if preference else LearningConfiguration.current().daily_target,
            "version": preference.version if preference else 0}


def check_version(expected, current):
    if expected != current["version"]:
        raise BusinessError("VERSION_CONFLICT", "学习记录已更新，请同步后重试。", 409, fields={"current": current})


def _active_user(user):
    locked = lock_user(user)
    if not locked.is_active or locked.is_staff or locked.is_superuser:
        raise BusinessError("INVALID_TOKEN", "学员账号已不可用，请重新登录。", 401)
    return locked


@transaction.atomic
def update_question_state(user, question_id, payload, key):
    from content.access import can_access_question
    user = _active_user(user)
    request_payload = {"questionId": str(question_id), **payload}
    replay = lookup(user, "learning-question-state", key, request_payload)
    if replay:
        return replay.response
    question = Question.objects.filter(pk=question_id).first()
    if question is None:
        raise BusinessError("NOT_FOUND", "题目不存在。", 404)
    state = QuestionState.objects.select_for_update().filter(user=user, question=question).first()
    current = state_snapshot(state, question_id)
    check_version(payload["baseVersion"], current)
    new_true = any(payload.get(field) is True and not current[field] for field in ("favorite", "mastered"))
    if (state is None or new_true) and not can_access_question(user, question):
        raise BusinessError("ENTITLEMENT_REQUIRED", "该题当前不可访问，不能新增学习状态。", 403)
    state = state or QuestionState(user=user, question=question)
    was_mastered = state.mastered
    for field in ("favorite", "mastered"):
        if field in payload:
            setattr(state, field, payload[field])
    state.version += 1
    state.save()
    if not was_mastered and state.mastered:
        LearningActivity.objects.get_or_create(user=user, question=question, study_date=shanghai_today())
    response = state_snapshot(state, question_id)
    remember(user, "learning-question-state", key, request_payload, response)
    return response


@transaction.atomic
def update_preferences(user, payload, key):
    from content.models import LearningConfiguration
    user = _active_user(user)
    replay = lookup(user, "learning-preferences", key, payload)
    if replay:
        return replay.response
    current = preference_snapshot(user)
    check_version(payload["baseVersion"], current)
    preference = LearningPreference.objects.select_for_update().filter(user=user).first()
    preference = preference or LearningPreference(user=user, daily_target=LearningConfiguration.current().daily_target)
    if "mode" in payload:
        preference.mode = payload["mode"]
    if "dailyTarget" in payload:
        preference.daily_target = payload["dailyTarget"]
    preference.version += 1
    preference.save()
    response = {"mode": preference.mode, "dailyTarget": preference.daily_target, "version": preference.version}
    remember(user, "learning-preferences", key, payload, response)
    return response


def statistics(user):
    from content.access import published_questions
    published = published_questions().annotate(learned=Exists(
        QuestionState.objects.filter(user=user, mastered=True, question_id=OuterRef("pk")),
    ))
    counts = published.aggregate(total=Count("pk"), mastered=Count("pk", filter=Q(learned=True)))
    total, mastered_count = counts["total"], counts["mastered"]
    activities = LearningActivity.objects.filter(user=user)
    articles = published.filter(published_revision__source=Source.CLASSICAL).order_by().values(
        "published_revision__article_id",
    ).annotate(total=Count("pk"), mastered=Count("pk", filter=Q(learned=True)))
    completed = sum(1 for item in articles if item["total"] > 0 and item["mastered"] == item["total"])
    return {
        "totalQuestions": total, "masteredQuestions": mastered_count,
        "masteredPercent": round(mastered_count * 100 / total, 2) if total else 0,
        "todayQuestions": activities.filter(study_date=shanghai_today()).count(),
        "studyDays": activities.values("study_date").distinct().count(),
        "completedArticles": completed, "dailyTarget": preference_snapshot(user)["dailyTarget"],
    }
