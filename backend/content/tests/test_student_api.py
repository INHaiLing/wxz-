import hashlib
import secrets
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import StudentSession
from content.models import Article, LearningConfiguration, Question
from entitlements.services import grant_entitlement, revoke_entitlement
from .test_student_access import StudentContentFixture


@override_settings(ROOT_URLCONF="content.tests.student_test_urls")
class StudentContentAPITests(StudentContentFixture):
    def setUp(self):
        self.client = APIClient()
        self.student = get_user_model().objects.create_user(username="p05-student")
        self.operator = get_user_model().objects.create_superuser(username="p05-operator", password="test-only-p05-password")
        self.token = secrets.token_urlsafe(32)
        self.session = StudentSession.objects.create(
            user=self.student, token_digest=hashlib.sha256(self.token.encode()).hexdigest(),
            encrypted_session_key="test-fixture-ciphertext-not-used-by-content",
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.questions_url = "/api/student/v1/questions/?source=classical&articleId=quanxue"

    def authenticate(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token)

    def test_public_list_and_details_share_free_set_without_paid_content_leak(self):
        response = self.client.get(self.questions_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data["results"]], [self.first.pk, self.second.pk])
        self.assertEqual(response.data["access"], {
            "activated": False, "pageSize": 2, "freeCount": 2, "totalCount": 3, "restrictedCount": 1,
        })
        self.assertIsNone(response.data["next"])
        self.assertIn("no-store", response["Cache-Control"])
        free_detail = self.client.get(f"/api/student/v1/questions/{self.first.pk}/")
        self.assertEqual(free_detail.status_code, 200)
        self.assertTrue(free_detail.data["access"]["free"])
        paid_detail = self.client.get(f"/api/student/v1/questions/{self.paid.pk}/")
        self.assertEqual(paid_detail.status_code, 403)
        self.assertEqual(paid_detail.data["error"]["code"], "ENTITLEMENT_REQUIRED")
        self.assertNotIn(self.paid.stem, str(paid_detail.data))
        self.assertNotIn(self.paid.answers[0], str(paid_detail.data))

    def test_translation_does_not_receive_an_additional_free_page(self):
        response = self.client.get(self.questions_url + "&type=translation")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])
        self.assertEqual(response.data["access"]["restrictedCount"], 1)
        self.assertEqual(self.client.get(self.questions_url + "&page=2").status_code, 404)

    def test_request_metadata_and_content_use_one_entitlement_decision(self):
        self.authenticate()
        for url in (self.questions_url, f"/api/student/v1/questions/{self.first.pk}/"):
            with self.subTest(url=url):
                # Simulate a grant arriving between independent policy reads.
                with patch("entitlements.services.has_active_entitlement", side_effect=[False, True]) as policy:
                    response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.data["access"]["activated"])
                self.assertEqual(policy.call_count, 1)
                if "results" in response.data:
                    self.assertEqual(response.data["count"], 2)

    def test_real_entitlement_grant_and_revocation_change_every_read(self):
        self.authenticate()
        source = grant_entitlement(self.student, "activation", "p05-redemption", actor=self.operator)
        response = self.client.get(self.questions_url)
        self.assertEqual(response.data["count"], 3)
        self.assertTrue(response.data["access"]["activated"])
        next_page = self.client.get(response.data["next"])
        self.assertEqual([row["id"] for row in next_page.data["results"]], [self.paid.pk])
        self.assertEqual(self.client.get(f"/api/student/v1/questions/{self.paid.pk}/").status_code, 200)
        revoke_entitlement(source.pk, self.operator, "测试撤销误发权益")
        response = self.client.get(f"/api/student/v1/questions/{self.paid.pk}/")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(self.paid.answers[0], str(response.data))
        self.assertEqual(self.client.get(self.questions_url).data["count"], 2)

    def test_revoke_one_source_does_not_block_another_valid_source(self):
        self.authenticate()
        first = grant_entitlement(self.student, "activation", "p05-source-a")
        grant_entitlement(self.student, "payment", "p05-source-b")
        revoke_entitlement(first.pk, self.operator, "保留另一有效来源")
        self.assertEqual(self.client.get(f"/api/student/v1/questions/{self.paid.pk}/").status_code, 200)

    def test_invalid_expired_disabled_and_revoked_tokens_never_fall_back_to_anonymous(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
        self.assertEqual(self.client.get("/api/student/v1/config/").status_code, 401)
        self.authenticate()
        for change in (
            {"expires_at": timezone.now() - timedelta(seconds=1)},
            {"expires_at": timezone.now() + timedelta(days=7), "revoked_at": timezone.now()},
        ):
            StudentSession.objects.filter(pk=self.session.pk).update(**change)
            self.assertEqual(self.client.get(self.questions_url).status_code, 401)
        StudentSession.objects.filter(pk=self.session.pk).update(revoked_at=None)
        get_user_model().objects.filter(pk=self.student.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.questions_url).status_code, 401)

    def test_staff_cookie_cannot_unlock_student_paid_content(self):
        grant_entitlement(self.operator, "payment", "operator-test-source")
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(f"/api/student/v1/questions/{self.paid.pk}/").status_code, 403)

    def test_strict_scope_query_validation(self):
        invalid_urls = [
            "/api/student/v1/questions/", "/api/student/v1/questions/?source=classical",
            "/api/student/v1/questions/?source=classical&categoryId=pre-qin",
            self.questions_url + "&categoryId=pre-qin", self.questions_url + "&articleId=shishuo",
            self.questions_url + "&type=", self.questions_url + "&type=unknown",
            self.questions_url + "&page=last", self.questions_url + "&page=0",
            self.questions_url + "&page_size=100", self.questions_url + "&ordering=-id",
            self.questions_url + "&limit=100", self.questions_url + "&page=1&page=2",
            "/api/student/v1/questions/?source=literature&categoryId=pre-qin&type=word",
            "/api/student/v1/questions/?source=unknown&articleId=quanxue",
            "/api/student/v1/questions/?source=classical&articleId=",
        ]
        for url in invalid_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 400)
        self.assertEqual(self.client.get(self.questions_url.replace("quanxue", "not-found")).status_code, 404)
        self.assertEqual(self.client.get(f"/api/student/v1/questions/{self.first.pk}/?type=word").status_code, 400)

    def test_config_read_is_nonmutating_and_admin_capacity_changes_apply_next_request(self):
        LearningConfiguration.objects.all().delete()
        response = self.client.get("/api/student/v1/config/")
        self.assertEqual(response.data, {"pageSize": 20, "dailyTarget": 20, "examDate": None})
        self.assertFalse(LearningConfiguration.objects.exists())
        LearningConfiguration.objects.create(page_size=1)
        response = self.client.get(self.questions_url)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["access"]["pageSize"], 1)
        self.assertEqual(self.client.get(f"/api/student/v1/questions/{self.second.pk}/").status_code, 403)

    def test_catalog_counts_follow_published_snapshots_and_hide_disabled_scope(self):
        response = self.client.get("/api/student/v1/articles/")
        quanxue = next(row for row in response.data["results"] if row["id"] == self.article.pk)
        self.assertEqual((quanxue["publishedCount"], quanxue["freeCount"]), (3, 2))
        self.first.article = self.other_article
        self.first.save()
        response = self.client.get("/api/student/v1/articles/")
        quanxue = next(row for row in response.data["results"] if row["id"] == self.article.pk)
        self.assertEqual(quanxue["publishedCount"], 3)
        Question.objects.filter(pk=self.first.pk).update(is_published=False)
        self.assertEqual(self.client.get(f"/api/student/v1/questions/{self.first.pk}/").status_code, 404)
        Article.objects.filter(pk=self.article.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.questions_url).status_code, 404)
        self.assertEqual(self.client.get(f"/api/student/v1/questions/{self.paid.pk}/").status_code, 404)

    def test_methods_and_config_admin_permissions_are_preserved(self):
        self.assertEqual(self.client.post(self.questions_url, {}).status_code, 405)
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get("/admin/content/learningconfiguration/add/").status_code, 403)
        self.assertEqual(self.client.get("/admin/content/learningconfiguration/1/delete/").status_code, 403)
        self.assertEqual(self.client.get("/admin/content/learningconfiguration/1/change/").status_code, 200)
