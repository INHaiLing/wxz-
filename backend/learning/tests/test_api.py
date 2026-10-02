from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from common.errors import BusinessError
from common.models import IdempotencyRecord
from content.models import Article, Question
from entitlements.services import grant_entitlement, revoke_entitlement
from learning.models import LearningActivity, LearningPreference, QuestionState
from learning.services import update_question_state
from .fixtures import build_fixture, make_question, make_session, publish


@override_settings(ROOT_URLCONF="learning.tests.urls")
class LearningAPITests(TestCase):
    def setUp(self):
        build_fixture(self)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token)

    def state(self, question, payload, key="learning-request-001"):
        return self.client.put(f"/api/student/v1/me/questions/{question.pk}/state/", payload,
                               format="json", HTTP_IDEMPOTENCY_KEY=key)

    def preferences(self, payload, key="preference-request-001"):
        return self.client.put("/api/student/v1/me/preferences/", payload,
                               format="json", HTTP_IDEMPOTENCY_KEY=key)

    def test_independent_explicit_flags_and_structured_version_conflict(self):
        first = self.state(self.first, {"favorite": True, "baseVersion": 0})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data, {"questionId": "first", "favorite": True, "mastered": False, "version": 1})
        conflict = self.state(self.first, {"mastered": True, "baseVersion": 0}, "another-device-001")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data["error"]["fields"]["current"], first.data)
        self.assertIs(type(conflict.data["error"]["fields"]["current"]["version"]), int)
        self.assertIs(type(conflict.data["error"]["fields"]["current"]["favorite"]), bool)
        second = self.state(self.first, {"mastered": True, "baseVersion": 1}, "learning-request-002")
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data["favorite"])
        self.assertTrue(second.data["mastered"])

    def test_historical_success_replay_does_not_reapply_after_later_cancel(self):
        payload = {"favorite": True, "mastered": True, "baseVersion": 0}
        first = self.state(self.first, payload)
        self.state(self.first, {"favorite": False, "mastered": False, "baseVersion": 1}, "cancel-request-001")
        replay = self.state(self.first, payload)
        self.assertEqual(replay.data, first.data)
        state = QuestionState.objects.get(user=self.student, question=self.first)
        self.assertEqual((state.favorite, state.mastered, state.version), (False, False, 2))
        self.assertEqual(LearningActivity.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 2)

    def test_same_key_different_payload_or_question_conflicts_without_mutation(self):
        self.state(self.first, {"favorite": True, "baseVersion": 0})
        for question, payload in ((self.first, {"favorite": False, "baseVersion": 1}),
                                  (self.second, {"favorite": True, "baseVersion": 0})):
            response = self.state(question, payload)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data["error"]["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(QuestionState.objects.count(), 1)

    def test_failed_version_request_can_retry_same_key_with_current_version(self):
        self.state(self.first, {"favorite": True, "baseVersion": 0})
        wrong = self.state(self.first, {"mastered": True, "baseVersion": 0}, "retry-current-001")
        self.assertEqual(wrong.status_code, 409)
        self.assertFalse(IdempotencyRecord.objects.filter(key="retry-current-001").exists())
        fixed = self.state(self.first, {"mastered": True, "baseVersion": 1}, "retry-current-001")
        self.assertEqual(fixed.status_code, 200)

    def test_strict_json_state_validation_rejects_types_unknown_fields_and_missing_key(self):
        for payload in (
            {"favorite": "true", "baseVersion": 0}, {"favorite": 1, "baseVersion": 0},
            {"favorite": True, "baseVersion": "0"}, {"favorite": True, "baseVersion": False},
            {"favorite": True, "baseVersion": -1}, {"favorite": True, "baseVersion": 2**63 - 1},
            {"baseVersion": 0}, {"favorite": True},
            {"favorite": True, "baseVersion": 0, "userId": self.other_student.pk},
            {"mastered": True, "baseVersion": 0, "studyDate": "2001-01-01"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.state(self.first, payload).status_code, 400)
        missing = self.client.put(f"/api/student/v1/me/questions/{self.first.pk}/state/",
                                  {"favorite": True, "baseVersion": 0}, format="json")
        self.assertEqual(missing.status_code, 400)
        self.assertFalse(QuestionState.objects.exists())

    def test_anonymous_staff_cookie_and_invalid_bearer_cannot_read_or_write_personal_data(self):
        urls = ["/api/student/v1/me/preferences/", "/api/student/v1/me/statistics/", "/api/student/v1/me/favorites/"]
        self.client.credentials()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 401)
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(urls[0]).status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
        self.assertEqual(self.client.get(urls[0]).status_code, 401)
        self.assertEqual(self.state(self.first, {"favorite": True, "baseVersion": 0}).status_code, 401)

    def test_account_isolation_and_user_specific_idempotency(self):
        self.state(self.first, {"favorite": True, "mastered": True, "baseVersion": 0})
        self.preferences({"mode": "reciting", "dailyTarget": 4, "baseVersion": 0})
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + make_session(self.other_student))
        self.assertEqual(self.client.get("/api/student/v1/me/favorites/").data["count"], 0)
        self.assertEqual(self.client.get("/api/student/v1/me/statistics/").data["masteredQuestions"], 0)
        self.assertEqual(self.client.get("/api/student/v1/me/preferences/").data["version"], 0)
        self.assertEqual(self.state(self.first, {"favorite": False, "baseVersion": 0}).status_code, 200)
        self.assertTrue(QuestionState.objects.get(user=self.student).favorite)

    def test_paid_draft_unknown_and_false_creation_are_guarded(self):
        for question in (self.paid, self.draft):
            for enabled in (True, False):
                self.assertEqual(self.state(question, {"favorite": enabled, "baseVersion": 0}).status_code, 403)
        response = self.client.put("/api/student/v1/me/questions/missing/state/", {"favorite": True, "baseVersion": 0},
                                   format="json", HTTP_IDEMPOTENCY_KEY="unknown-question-001")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(QuestionState.objects.exists())

    def test_revocation_keeps_history_blocks_new_true_and_allows_existing_false(self):
        source = grant_entitlement(self.student, "activation", "learning-code-001")
        self.state(self.paid, {"favorite": True, "mastered": True, "baseVersion": 0})
        revoke_entitlement(source.pk, self.operator, "撤销误发权益")
        favorite = self.client.get("/api/student/v1/me/favorites/").data["results"][0]
        self.assertIsNone(favorite["question"])
        self.assertEqual(favorite["unavailableReason"], "ENTITLEMENT_REQUIRED")
        self.assertNotIn(self.paid.stem, str(favorite))
        self.assertNotIn(self.paid.answers[0], str(favorite))
        self.assertEqual(self.state(self.paid, {"favorite": False, "mastered": False, "baseVersion": 1}, "cancel-paid-001").status_code, 200)
        self.assertEqual(self.state(self.paid, {"mastered": True, "baseVersion": 2}, "repeat-paid-001").status_code, 403)
        self.assertEqual(LearningActivity.objects.count(), 1)
        self.assertEqual(self.client.get("/api/student/v1/me/favorites/").data["count"], 0)

    def test_down_or_inactive_favorite_has_only_placeholder_and_can_cancel(self):
        self.state(self.first, {"favorite": True, "mastered": True, "baseVersion": 0})
        Question.objects.filter(pk=self.first.pk).update(is_published=False)
        row = self.client.get("/api/student/v1/me/favorites/").data["results"][0]
        self.assertEqual(row["unavailableReason"], "CONTENT_UNAVAILABLE")
        self.assertIsNone(row["question"])
        self.assertEqual(row["state"]["questionId"], self.first.pk)
        self.assertEqual(self.state(self.first, {"favorite": False, "baseVersion": 1}, "cancel-down-001").status_code, 200)
        self.assertEqual(LearningActivity.objects.count(), 1)
        Article.objects.filter(pk=self.article.pk).update(is_active=False)
        self.assertEqual(self.state(self.second, {"mastered": True, "baseVersion": 0}, "inactive-scope-001").status_code, 403)

    def test_favorites_always_use_published_revision_not_draft_and_preserve_state(self):
        self.state(self.first, {"favorite": True, "mastered": True, "baseVersion": 0})
        self.first.stem = "仅存在于草稿{{0}}"
        self.first.answers = ["仅存在于草稿的答案"]
        self.first.save()
        old = self.client.get("/api/student/v1/me/favorites/").data["results"][0]
        self.assertNotEqual(old["question"]["stem"], self.first.stem)
        publish(self.first)
        new = self.client.get("/api/student/v1/me/favorites/").data["results"][0]
        self.assertEqual(new["question"]["stem"], self.first.stem)
        self.assertEqual(new["state"], old["state"])
        self.assertEqual(LearningActivity.objects.count(), 1)

    def test_free_capacity_change_does_not_leak_existing_favorite(self):
        self.state(self.second, {"favorite": True, "baseVersion": 0})
        self.config.page_size = 1
        self.config.save()
        row = self.client.get("/api/student/v1/me/favorites/").data["results"][0]
        self.assertIsNone(row["question"])
        self.assertEqual(row["unavailableReason"], "ENTITLEMENT_REQUIRED")
        self.assertEqual(self.state(self.second, {"favorite": False, "baseVersion": 1}, "capacity-cancel-001").status_code, 200)

    def test_old_success_replay_after_revocation_does_not_restore_canceled_state(self):
        source = grant_entitlement(self.student, "activation", "historical-code-001")
        payload = {"favorite": True, "mastered": True, "baseVersion": 0}
        first = self.state(self.paid, payload)
        revoke_entitlement(source.pk, self.operator, "历史重放撤权测试")
        self.state(self.paid, {"favorite": False, "mastered": False, "baseVersion": 1}, "revoke-cancel-001")
        self.assertEqual(self.state(self.paid, payload).data, first.data)
        state = QuestionState.objects.get(user=self.student, question=self.paid)
        self.assertEqual((state.favorite, state.mastered, state.version), (False, False, 2))
        self.assertEqual(self.client.get("/api/student/v1/me/favorites/").data["count"], 0)

    def test_published_scope_moves_update_article_completion_without_changing_state(self):
        grant_entitlement(self.student, "activation", "move-article-code-001")
        for index, question in enumerate((self.first, self.second, self.paid)):
            self.state(question, {"mastered": True, "baseVersion": 0}, f"move-article-{index:03}")
        self.assertEqual(self.client.get("/api/student/v1/me/statistics/").data["completedArticles"], 1)
        self.paid.article = self.other_article
        self.paid.save()
        self.assertEqual(self.client.get("/api/student/v1/me/statistics/").data["completedArticles"], 1)
        publish(self.paid)
        stats = self.client.get("/api/student/v1/me/statistics/").data
        self.assertEqual((stats["completedArticles"], stats["masteredQuestions"]), (1, 3))
        self.assertEqual(QuestionState.objects.get(user=self.student, question=self.paid).version, 1)

    def test_favorites_paginate_all_own_states_without_client_capacity_override(self):
        grant_entitlement(self.student, "activation", "pagination-code-001")
        for index, question in enumerate((self.first, self.second, self.paid)):
            self.state(question, {"favorite": True, "baseVersion": 0}, f"favorite-page-{index:03}")
        first = self.client.get("/api/student/v1/me/favorites/")
        self.assertEqual(first.data["count"], 3)
        self.assertEqual(len(first.data["results"]), 2)
        self.assertEqual(len(self.client.get(first.data["next"]).data["results"]), 1)
        self.assertIn("no-store", first["Cache-Control"])
        self.assertIn("Authorization", first["Vary"])
        for query in ("page_size=100", "ordering=id", "page=last", "page=0", "page=1&page=2", "type=word"):
            self.assertEqual(self.client.get("/api/student/v1/me/favorites/?" + query).status_code, 400)

    def test_default_preferences_get_has_no_writes_and_uses_backend_target(self):
        response = self.client.get("/api/student/v1/me/preferences/")
        self.assertEqual(response.data, {"mode": "writing", "dailyTarget": 27, "version": 0})
        self.assertFalse(LearningPreference.objects.exists())
        self.config.daily_target = 31
        self.config.save()
        self.assertEqual(self.client.get("/api/student/v1/me/preferences/").data["dailyTarget"], 31)

    def test_preferences_explicit_write_conflict_replay_and_saved_target_survives_default_change(self):
        payload = {"mode": "reciting", "dailyTarget": 5, "baseVersion": 0}
        first = self.preferences(payload)
        self.assertEqual(first.data, {"mode": "reciting", "dailyTarget": 5, "version": 1})
        conflict = self.preferences({"mode": "writing", "baseVersion": 0}, "preference-other-001")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data["error"]["fields"]["current"], first.data)
        self.assertEqual(self.preferences(payload).data, first.data)
        self.config.daily_target = 40
        self.config.save()
        self.assertEqual(self.client.get("/api/student/v1/me/statistics/").data["dailyTarget"], 5)
        self.assertEqual(self.preferences({"dailyTarget": 7, "baseVersion": 1}).status_code, 409)

    def test_preferences_validation_and_read_query_rejection(self):
        for payload in ({"mode": "recite", "baseVersion": 0}, {"mode": "writing"}, {"baseVersion": 0},
                        {"dailyTarget": 0, "baseVersion": 0}, {"dailyTarget": 1001, "baseVersion": 0},
                        {"dailyTarget": "5", "baseVersion": 0}, {"dailyTarget": True, "baseVersion": 0},
                        {"mode": "writing", "baseVersion": 0, "userId": 1}):
            self.assertEqual(self.preferences(payload).status_code, 400)
        for url in ("me/preferences/", "me/statistics/"):
            self.assertEqual(self.client.get("/api/student/v1/" + url + "?userId=1").status_code, 400)
        self.assertFalse(LearningPreference.objects.exists())

    def test_shanghai_boundary_and_same_day_remaster_count_once(self):
        with patch("learning.services.timezone.now", return_value=datetime(2026, 10, 1, 15, 59, tzinfo=dt_timezone.utc)):
            self.state(self.first, {"mastered": True, "baseVersion": 0})
            self.state(self.first, {"mastered": False, "baseVersion": 1}, "day-cancel-001")
            self.state(self.first, {"mastered": True, "baseVersion": 2}, "day-remaster-001")
            self.assertEqual(self.client.get("/api/student/v1/me/statistics/").data["todayQuestions"], 1)
        with patch("learning.services.timezone.now", return_value=datetime(2026, 10, 1, 16, 0, tzinfo=dt_timezone.utc)):
            self.state(self.first, {"mastered": False, "baseVersion": 3}, "newday-cancel-001")
            self.state(self.first, {"mastered": True, "baseVersion": 4}, "newday-master-001")
            stats = self.client.get("/api/student/v1/me/statistics/").data
        self.assertEqual((stats["todayQuestions"], stats["studyDays"]), (1, 2))
        self.assertEqual([str(day) for day in LearningActivity.objects.order_by("study_date").values_list("study_date", flat=True)],
                         ["2026-10-01", "2026-10-02"])

    def test_statistics_use_all_current_published_and_nonempty_complete_articles(self):
        grant_entitlement(self.student, "activation", "statistics-code-001")
        for index, question in enumerate((self.first, self.second, self.paid, self.other)):
            self.state(question, {"mastered": True, "baseVersion": 0}, f"statistics-master-{index:03}")
        stats = self.client.get("/api/student/v1/me/statistics/").data
        self.assertEqual((stats["totalQuestions"], stats["masteredQuestions"], stats["completedArticles"]), (4, 4, 2))
        self.assertEqual(stats["masteredPercent"], 100)
        make_question("new-publication", self.article, order=4)
        stats = self.client.get("/api/student/v1/me/statistics/").data
        self.assertEqual((stats["totalQuestions"], stats["masteredQuestions"], stats["completedArticles"]), (5, 4, 1))
        self.assertEqual(stats["masteredPercent"], 80)
        Question.objects.update(is_published=False)
        stats = self.client.get("/api/student/v1/me/statistics/").data
        self.assertEqual((stats["totalQuestions"], stats["masteredQuestions"], stats["completedArticles"], stats["masteredPercent"]), (0, 0, 0, 0))
        self.assertEqual(stats["todayQuestions"], 4)
        self.assertEqual(stats["studyDays"], 1)

    def test_transaction_failure_rolls_back_state_activity_and_replay(self):
        with patch("learning.services.remember", side_effect=RuntimeError("simulated durable write failure")):
            with self.assertRaises(RuntimeError):
                update_question_state(self.student, self.first.pk, {"mastered": True, "baseVersion": 0}, "rollback-write-001")
        self.assertFalse(QuestionState.objects.exists())
        self.assertFalse(LearningActivity.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_locked_user_rechecks_account_disabled_or_promoted_after_authentication(self):
        for values in ({"is_active": False}, {"is_active": True, "is_staff": True},
                       {"is_staff": False, "is_superuser": True}):
            get_user_model().objects.filter(pk=self.student.pk).update(**values)
            with self.assertRaises(BusinessError) as caught:
                update_question_state(self.student, self.first.pk, {"favorite": True, "baseVersion": 0}, "disabled-race-001")
            self.assertEqual(str(caught.exception.detail["error"]["code"]), "INVALID_TOKEN")
        self.assertFalse(QuestionState.objects.exists())
