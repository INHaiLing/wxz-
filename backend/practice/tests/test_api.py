import hashlib
import secrets
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import StudentSession
from common.models import IdempotencyRecord, RateBucket
from content.models import Article, LearningConfiguration, Question
from content.tests.test_student_access import StudentContentFixture
from entitlements.services import grant_entitlement, revoke_entitlement
from practice.models import PracticeRound, ResumePosition, RoundItem


@override_settings(ROOT_URLCONF="practice.tests.urls")
class PracticeAPITests(StudentContentFixture):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="p08-student")
        self.other_user = get_user_model().objects.create_user(username="p08-other")
        self.operator = get_user_model().objects.create_superuser(username="p08-operator", password="test-fixture-only-password")
        self.client = APIClient()
        self.token = self.token_for(self.user)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token)
        self.scope = {"source": "classical", "articleId": self.article.pk, "mode": "writing"}
        self.create_url = "/api/student/v1/practice/rounds/"
        self.resume_url = "/api/student/v1/me/resume/"

    def token_for(self, user):
        token = secrets.token_urlsafe(32)
        StudentSession.objects.create(user=user, token_digest=hashlib.sha256(token.encode()).hexdigest(),
                                      encrypted_session_key="test-fixture-not-used-by-practice",
                                      expires_at=timezone.now() + timedelta(days=7))
        return token

    def create_round(self, payload=None, key="p08-round-key", expected=201):
        with patch("practice.services.random.SystemRandom.shuffle", side_effect=lambda rows: None):
            response = self.client.post(self.create_url, payload or self.scope, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(response.status_code, expected, response.data)
        return response

    def group(self, round_id, index=1):
        return self.client.get(f"{self.create_url}{round_id}/groups/{index}/")

    def save_resume(self, question=None, version=0, key="p08-resume-key", **context):
        payload = {**self.scope, "questionId": (question or self.first).pk, "baseVersion": version, **context}
        return self.client.put(self.resume_url, payload, format="json", HTTP_IDEMPOTENCY_KEY=key)

    def test_round_free_set_does_not_expand_after_type_switch(self):
        created = self.create_round()
        self.assertEqual((created.data["totalCount"], created.data["groupCount"], created.data["pageSize"]), (2, 1, 2))
        self.assertNotIn("answers", str(created.data))
        response = self.group(created.data["id"])
        self.assertEqual([q["id"] for q in response.data["results"]], [self.first.pk, self.second.pk])
        self.assertEqual(response.data["skipped"], [])
        self.create_round({**self.scope, "type": "translation"}, key="p08-translation", expected=403)
        word = self.create_round({**self.scope, "type": "word"}, key="p08-word-key")
        self.assertEqual(word.data["totalCount"], 1)
        self.assertEqual(PracticeRound.objects.count(), 2)

    def test_randomization_is_persisted_and_original_groups_survive_capacity_changes(self):
        grant_entitlement(self.user, "activation", "p08-full")
        with patch("practice.services.random.SystemRandom.shuffle", side_effect=lambda rows: rows.reverse()) as shuffle:
            response = self.client.post(self.create_url, self.scope, format="json", HTTP_IDEMPOTENCY_KEY="p08-shuffle-key")
        self.assertEqual(shuffle.call_count, 1)
        round_id = response.data["id"]
        before = self.group(round_id).data
        self.assertEqual([q["id"] for q in before["results"]], [self.paid.pk, self.second.pk])
        self.assertEqual([q["id"] for q in self.group(round_id, 2).data["results"]], [self.first.pk])
        config = LearningConfiguration.current()
        config.page_size = 1
        config.save()
        self.assertEqual(self.group(round_id).data, before)
        self.assertEqual(self.group(round_id, 3).status_code, 404)

    def test_round_retry_reuses_history_and_conflicting_key_is_rejected(self):
        first = self.create_round()
        self.assertEqual(self.create_round().data, first.data)
        self.assertEqual(PracticeRound.objects.count(), 1)
        self.assertEqual(RoundItem.objects.count(), 2)
        conflict = self.create_round({**self.scope, "mode": "reciting"}, expected=409)
        self.assertEqual(conflict.data["error"]["code"], "IDEMPOTENCY_CONFLICT")
        Question.objects.filter(pk=self.first.pk).update(is_published=False)
        self.assertEqual(self.create_round().data, first.data)
        self.assertEqual(PracticeRound.objects.count(), 1)

    def test_fixed_revision_survives_republication_and_draft_without_adding_new_questions(self):
        round_id = self.create_round().data["id"]
        original = self.group(round_id).data["results"][0]
        self.first.stem = "新版题干{{0}}"
        self.first.answers = ["新版答案"]
        self.publish(self.first)
        self.make_question("later-question", order=100)
        self.assertEqual(self.group(round_id).data["results"][0], original)
        self.first.answers = ["未发布草稿"]
        self.first.save()
        self.assertEqual(self.group(round_id).data["results"][0], original)
        self.assertEqual(RoundItem.objects.count(), 2)

    def test_withdrawal_and_changed_current_scope_skip_in_original_positions(self):
        round_id = self.create_round().data["id"]
        Question.objects.filter(pk=self.first.pk).update(is_published=False)
        self.second.article = self.other_article
        self.publish(self.second)
        response = self.group(round_id)
        self.assertEqual(response.data["results"], [])
        self.assertEqual(response.data["skipped"], [{"position": 1, "reason": "UNAVAILABLE"}, {"position": 2, "reason": "SCOPE_CHANGED"}])
        self.assertNotIn(self.paid.answers[0], str(response.data))
        self.assertEqual(response.data["totalCount"], 2)

    def test_revoke_and_current_free_capacity_prevent_old_round_bypass(self):
        source = grant_entitlement(self.user, "activation", "p08-revoke")
        round_id = self.create_round().data["id"]
        revoke_entitlement(source.pk, self.operator, "P08 误发撤销测试")
        self.assertEqual(self.group(round_id, 2).data["skipped"], [{"position": 3, "reason": "ENTITLEMENT_REQUIRED"}])
        self.assertEqual(self.group(round_id, 2).data["results"], [])
        config = LearningConfiguration.current()
        config.page_size = 1
        config.save()
        response = self.group(round_id)
        self.assertEqual([q["id"] for q in response.data["results"]], [self.first.pk])
        self.assertEqual(response.data["skipped"], [{"position": 2, "reason": "ENTITLEMENT_REQUIRED"}])
        self.assertNotIn(self.second.answers[0], str(response.data))

    def test_current_type_change_and_inactive_catalog_revoke_saved_access(self):
        round_id = self.create_round({**self.scope, "type": "word"}).data["id"]
        self.first.type = "fact"
        self.publish(self.first)
        self.assertEqual(self.group(round_id).data["skipped"][0]["reason"], "SCOPE_CHANGED")
        Article.objects.filter(pk=self.article.pk).update(is_active=False)
        self.assertEqual(self.group(round_id).data["results"], [])

    def test_rounds_require_authentication_ownership_and_do_not_expose_through_queries(self):
        round_id = self.create_round().data["id"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token_for(self.other_user))
        self.assertEqual(self.group(round_id).status_code, 404)
        self.client.credentials()
        self.assertEqual(self.client.get(self.resume_url).status_code, 401)
        self.assertEqual(self.group(round_id).status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
        self.assertEqual(self.client.get(self.resume_url).status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token)
        for suffix in ("?type=word", "?page_size=100", "?mode=writing&mode=reciting"):
            self.assertEqual(self.client.get(f"{self.create_url}{round_id}/groups/1/{suffix}").status_code, 400)
        self.assertEqual(self.group(round_id, 0).status_code, 404)

    def test_strict_creation_validation_no_partial_round_or_idempotency_rows(self):
        cases = [[], {**self.scope, "mode": "dictation"}, {**self.scope, "source": []},
                 {**self.scope, "articleId": 123}, {**self.scope, "categoryId": "pre-qin"},
                 {**self.scope, "pageSize": 100}, {"source": "literature", "categoryId": "pre-qin", "type": "word", "mode": "writing"}]
        for index, payload in enumerate(cases):
            with self.subTest(payload=payload):
                response = self.client.post(self.create_url, payload, format="json", HTTP_IDEMPOTENCY_KEY=f"p08-invalid-{index}")
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(PracticeRound.objects.count(), 0)
        self.assertEqual(IdempotencyRecord.objects.count(), 0)
        self.assertEqual(self.client.post(self.create_url, self.scope, format="json").status_code, 400)

    def test_empty_scope_and_resource_limit_do_not_create_partial_records(self):
        empty = Article.objects.create(id="empty", title="空篇目")
        self.create_round({**self.scope, "articleId": empty.pk}, expected=404)
        with patch("practice.services.MAX_ROUND_SIZE", 1):
            self.create_round(key="p08-too-large", expected=400)
        self.assertFalse(PracticeRound.objects.exists())
        self.assertFalse(RoundItem.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_rate_limit_counts_failed_business_requests(self):
        empty = Article.objects.create(id="empty", title="空篇目")
        for index in range(10):
            self.create_round({**self.scope, "articleId": empty.pk}, key=f"p08-rate-{index}", expected=404)
        self.create_round(key="p08-rate-eleven", expected=429)
        self.assertEqual(RateBucket.objects.get(scope="practice.round.create").count, 10)

    def test_resume_get_does_not_write_and_successful_sequence_write_has_typed_context(self):
        response = self.client.get(self.resume_url)
        self.assertEqual(response.data, {"version": 0, "resume": None, "valid": False, "reason": "NO_RESUME", "start": None})
        self.assertFalse(ResumePosition.objects.exists())
        response = self.save_resume()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["version"], 1)
        self.assertEqual(response.data["resume"]["questionId"], self.first.pk)
        current = self.client.get(self.resume_url)
        self.assertTrue(current.data["valid"])
        self.assertEqual(current.data["start"], current.data["resume"])
        self.assertIn("no-store", current["Cache-Control"])
        self.assertIn("Authorization", current["Vary"])
        self.assertNotIn("answers", str(current.data))

    def test_resume_version_conflict_and_retry_do_not_overwrite_later_device(self):
        first = self.save_resume()
        second = self.save_resume(self.second, version=1, key="p08-resume-next", mode="reciting")
        self.assertEqual(second.data["version"], 2)
        conflict = self.save_resume(self.first, version=1, key="p08-resume-stale")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data["error"]["fields"]["current"]["version"], 2)
        replay = self.save_resume()
        self.assertEqual(replay.data["version"], first.data["version"])
        self.assertEqual(replay.data["current"]["version"], 2)
        self.assertEqual(ResumePosition.objects.get(user=self.user).question_id, self.second.pk)

    def test_resume_rejects_paid_draft_wrong_scope_and_arbitrary_routes(self):
        for question, extra, status in ((self.paid, {}, 403), (self.draft, {}, 400),
                                         (self.other, {}, 400), (self.first, {"route": "/pages/secret"}, 400),
                                         (self.first, {"baseVersion": True}, 400),
                                         (self.first, {"roundId": 1, "groupIndex": 1}, 400)):
            with self.subTest(question=question.pk, extra=extra):
                response = self.save_resume(question, **extra)
                self.assertEqual(response.status_code, status, response.data)
                self.assertNotIn(question.answers[0], str(response.data))
        self.assertFalse(ResumePosition.objects.exists())

    def test_random_resume_checks_owner_range_membership_and_original_group(self):
        from learning.models import LearningPreference

        preference = LearningPreference.objects.create(user=self.user, mode="writing", daily_target=37, version=2)
        grant_entitlement(self.user, "activation", "p08-random-resume")
        round_id = self.create_round().data["id"]
        wrong_group = self.save_resume(self.paid, roundId=round_id, groupIndex=1)
        self.assertEqual(wrong_group.status_code, 400)
        incomplete = self.save_resume(roundId=round_id)
        self.assertEqual(incomplete.status_code, 400)
        wrong_range = self.save_resume(roundId=round_id, groupIndex=1, type="word")
        self.assertEqual(wrong_range.status_code, 400)
        valid = self.save_resume(self.paid, roundId=round_id, groupIndex=2, mode="reciting")
        self.assertEqual(valid.status_code, 200, valid.data)
        self.assertEqual(PracticeRound.objects.get(pk=round_id).mode, "writing")
        self.assertEqual(valid.data["current"]["resume"]["mode"], "reciting")
        preference.refresh_from_db()
        self.assertEqual((preference.mode, preference.daily_target, preference.version), ("writing", 37, 2))
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token_for(self.other_user))
        self.assertEqual(self.save_resume(roundId=round_id, groupIndex=1).status_code, 404)

    def test_durable_replay_failure_rolls_back_round_items_and_resume_updates(self):
        from practice.serializers import ResumeRequest, RoundRequest
        from practice.services import create_round, save_resume

        request = RoundRequest(data=self.scope)
        request.is_valid(raise_exception=True)
        with patch("practice.services.remember", side_effect=RuntimeError("test durable failure")):
            with self.assertRaises(RuntimeError):
                create_round(self.user, request.validated_data, "p08-rollback-round")
        self.assertFalse(PracticeRound.objects.exists())
        self.assertFalse(RoundItem.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())
        self.assertEqual(self.save_resume().status_code, 200)
        request = ResumeRequest(data={**self.scope, "questionId": self.second.pk, "baseVersion": 1})
        request.is_valid(raise_exception=True)
        with patch("practice.services.remember", side_effect=RuntimeError("test durable failure")):
            with self.assertRaises(RuntimeError):
                save_resume(self.user, request.validated_data, "p08-rollback-resume")
        current = ResumePosition.objects.get(user=self.user)
        self.assertEqual((current.question_id, current.version), (self.first.pk, 1))
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_sequence_resume_after_revocation_returns_safe_start_and_never_restores_grant(self):
        source = grant_entitlement(self.user, "activation", "p08-sequence-revoke")
        successful = self.save_resume(self.paid)
        self.assertTrue(successful.data["current"]["valid"])
        revoke_entitlement(source.pk, self.operator, "测试续学撤销")
        current = self.client.get(self.resume_url)
        self.assertFalse(current.data["valid"])
        self.assertEqual(current.data["reason"], "ENTITLEMENT_REQUIRED")
        self.assertEqual(current.data["start"]["questionId"], self.first.pk)
        self.assertNotIn(self.paid.stem, str(current.data))
        self.assertNotIn(self.paid.answers[0], str(current.data))
        replay = self.save_resume(self.paid)
        self.assertEqual(replay.status_code, 200)
        self.assertFalse(replay.data["current"]["valid"])
        self.assertEqual(ResumePosition.objects.get(user=self.user).version, 1)

    def test_random_resume_fallback_only_uses_accessible_original_items(self):
        source = grant_entitlement(self.user, "activation", "p08-random-revoke")
        round_id = self.create_round().data["id"]
        self.assertEqual(self.save_resume(self.paid, roundId=round_id, groupIndex=2).status_code, 200)
        revoke_entitlement(source.pk, self.operator, "随机续学撤销")
        current = self.client.get(self.resume_url)
        self.assertEqual(current.data["start"]["questionId"], self.first.pk)
        self.assertEqual(current.data["start"]["groupIndex"], 1)
        Question.objects.filter(pk__in=[self.first.pk, self.second.pk]).update(is_published=False)
        Article.objects.filter(pk=self.article.pk).update(is_active=False)
        current = self.client.get(self.resume_url)
        self.assertIsNone(current.data["start"])
        self.assertEqual(current.data["version"], 1)

    def test_sequence_resume_scope_change_and_down_return_unavailable_without_write(self):
        self.save_resume()
        self.first.article = self.other_article
        self.publish(self.first)
        current = self.client.get(self.resume_url)
        self.assertEqual(current.data["reason"], "SCOPE_CHANGED")
        self.assertEqual(current.data["start"]["questionId"], self.second.pk)
        Question.objects.filter(pk=self.first.pk).update(is_published=False)
        self.assertEqual(self.client.get(self.resume_url).data["reason"], "UNAVAILABLE")
        self.assertEqual(ResumePosition.objects.get(user=self.user).version, 1)

    def test_model_write_guards_and_database_constraints_protect_history(self):
        round_id = self.create_round().data["id"]
        round_ = PracticeRound.objects.get(pk=round_id)
        for operation in (lambda: round_.save(_service=True), lambda: PracticeRound.objects.filter(pk=round_id).update(mode="reciting"), lambda: RoundItem.objects.all().delete()):
            with self.assertRaises(ValidationError):
                operation()
        self.save_resume()
        position = ResumePosition.objects.get(user=self.user)
        position.round = round_
        position.group_index = None
        with self.assertRaises(IntegrityError), transaction.atomic():
            position.save(_service=True)

    def test_admin_allows_view_only_and_writes_cannot_bypass_services(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from practice.admin import ReadOnlyPracticeAdmin

        request = RequestFactory().get("/admin/practice/practiceround/")
        request.user = self.operator
        admin = ReadOnlyPracticeAdmin(PracticeRound, AdminSite())
        self.assertTrue(admin.has_view_permission(request))
        self.assertFalse(admin.has_add_permission(request))
        self.assertFalse(admin.has_change_permission(request))
        self.assertFalse(admin.has_delete_permission(request))
