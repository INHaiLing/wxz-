"""Real module flows; only the external code2session boundary is replaced."""
import base64
import csv
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from tablib import Dataset
from rest_framework.test import APIClient

from accounts.wechat import WeChatLogin
from activation.models import ActivationCode, ActivationRedemption
from content.importing import QuestionXLSX
from content.models import Article, LearningConfiguration, Question
from content.resources import BASE_HEADERS
from contracts.validation import assert_response, load_document
from learning.models import LearningActivity


STUDENT_SETTINGS = {
    "STUDENT_SESSION_ENCRYPTION_KEYS": (Fernet.generate_key().decode(),),
    "WECHAT_APP_ID": "wx-contract-test",
    "WECHAT_APP_SECRET": "test-only-contract-secret",
    "WECHAT_LOGIN_RATE_LIMIT": 1000,
    "ACTIVATION_REDEEM_RATE_LIMIT": 1000,
}


class StudentFlowFixture:
    def setUp(self):
        self.operator = get_user_model().objects.create_superuser("contract-operator", password="test-only-contract-password")
        self.article = Article.objects.create(id="quanxue", title="劝学")
        LearningConfiguration.objects.create(page_size=2, daily_target=20)
        self.admin = Client()
        self.admin.force_login(self.operator)
        self.client = APIClient()
        self.folder = TemporaryDirectory(prefix="contracts-import-")
        self.addCleanup(self.folder.cleanup)
        self.temp_settings = override_settings(QUESTION_IMPORT_TMP_DIR=Path(self.folder.name))
        self.temp_settings.enable()
        self.addCleanup(self.temp_settings.disable)
        self.import_and_publish()

    def call(self, method, path, data=None, *, key=None, client=None, status=200):
        kwargs = {"format": "json"}
        if key:
            kwargs["HTTP_IDEMPOTENCY_KEY"] = key
        response = getattr(client or self.client, method.lower())(path, data, **kwargs)
        self.assertEqual(response.status_code, status, response.data if hasattr(response, "data") else response.content)
        assert_response(response, path, method)
        return response

    def import_and_publish(self):
        # Exercise the same XLSX upload/preview/confirm flow the operator uses.
        headers = [*BASE_HEADERS, "答案1"]
        dataset = Dataset(headers=headers)
        for index, kind in enumerate(("word", "fact", "translation"), 1):
            dataset.append([f"contract-{index}", "classical", "", self.article.pk, kind, "示例", index,
                            f"第{index}题{{{{0}}}}。", f"第{index}题答案"])
        upload = SimpleUploadedFile("contract.xlsx", QuestionXLSX().export_data(dataset),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        preview = self.admin.post(reverse("admin:content_question_import"), {"import_file": upload, "format": "0", "resource": "0"})
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(Question.objects.exists())
        confirm = self.admin.post(reverse("admin:content_question_process_import"), dict(preview.context["confirm_form"].initial))
        self.assertEqual(confirm.status_code, 302)
        self.assertEqual(Question.objects.count(), 3)
        self.assertFalse(Question.objects.filter(is_published=True).exists())
        before = self.client.get("/api/student/v1/questions/?source=classical&articleId=quanxue")
        self.assertEqual(before.data["count"], 0)
        self.publish(list(Question.objects.values_list("pk", flat=True)))

    def publish(self, ids):
        url = reverse("admin:content_question_changelist")
        data = {"action": "publish_selected", "_selected_action": ids}
        preview = self.admin.post(url, data)
        self.assertEqual(preview.status_code, 200)
        data.update(confirmation_token=preview.context["confirmation_token"], confirm_publication="publish_selected")
        self.assertEqual(self.admin.post(url, data).status_code, 302)

    def login(self, client=None, openid="contract-student"):
        client = client or self.client
        client.credentials()
        login = WeChatLogin("wx-contract-test", openid, base64.b64encode(b"test-contract-wx").decode())
        with patch("accounts.student_api.exchange_code", return_value=login):
            result = self.call("post", "/api/student/v1/auth/wechat/", {"code": "test-only-login-code"}, client=client)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + result.data["token"])
        return result.data["user"]["id"]

    def activation_codes(self):
        # Only a single HTTP response holds raw codes; the admin DB does not.
        result = self.admin.post(reverse("admin:activation_batch_generate"),
            {"quantity": 2, "label": "接口联调测试", "idempotency_key": "contract-code-batch"})
        self.assertEqual(result.status_code, 200)
        rows = list(csv.reader(StringIO(result.content.decode("utf-8-sig"))))
        batch_id = result["X-Activation-Batch-ID"]
        for action in ("receive", "enable"):
            url = reverse("admin:activation_batch_transition", args=(batch_id, action))
            preview = self.admin.get(url)
            self.assertEqual(self.admin.post(url, {"confirmation_token": preview.context["confirmation_token"], "confirm": "yes"}).status_code, 302)
        return [row[1] for row in rows[1:]]

    def revoke_activation(self):
        redemption = ActivationRedemption.objects.get()
        url = reverse("admin:entitlements_source_revoke", args=(redemption.source_id,))
        preview = self.admin.get(url)
        result = self.admin.post(url, {"confirmation_token": preview.context["confirmation_token"],
                                      "confirm": "yes", "reason": "接口联调：撤销误发来源"})
        self.assertEqual(result.status_code, 302)

    def groups(self, round_result):
        return [self.call("get", f"/api/student/v1/practice/rounds/{round_result['id']}/groups/{index}/").data
                for index in range(1, round_result["groupCount"] + 1)]


@override_settings(**STUDENT_SETTINGS)
class StudentEndToEndTests(StudentFlowFixture, TestCase):
    def test_complete_import_trial_identity_redemption_learning_and_revocation(self):
        query = "/api/student/v1/questions/?source=classical&articleId=quanxue"
        for url in ("config/", "categories/", "articles/", "products/"):
            self.call("get", "/api/student/v1/" + url)
        free = self.call("get", query)
        self.assertEqual([row["id"] for row in free.data["results"]], ["contract-1", "contract-2"])
        translated = self.call("get", query + "&type=translation")
        self.assertEqual(translated.data["results"], [])
        self.call("get", query + "&page_size=100", status=400)
        self.call("get", "/api/student/v1/questions/contract-3/", status=403)
        self.call("get", "/api/student/v1/questions/contract-1/")
        user_id = self.login()
        second_device = APIClient()
        self.assertEqual(self.login(second_device), user_id)
        self.call("get", "/api/student/v1/me/questions/contract-1/state/")
        codes = self.activation_codes()
        activation = self.call("post", "/api/student/v1/activation/redeem/", {"code": codes[0]}, key="contract-redeem-first")
        self.assertTrue(activation.data["entitlement"]["active"])
        self.assertIsNone(activation.data["entitlement"]["expiresAt"])
        self.call("post", "/api/student/v1/activation/redeem/", {"code": codes[1]}, key="contract-redeem-second", status=409)
        self.assertEqual(ActivationCode.objects.filter(state="unused").count(), 1)
        self.call("get", "/api/student/v1/me/entitlements/")
        self.assertEqual(self.call("get", query).data["count"], 3)
        self.call("get", "/api/student/v1/questions/contract-3/")
        state_url = "/api/student/v1/me/questions/contract-3/state/"
        initial = {"favorite": True, "mastered": True, "baseVersion": 0}
        success = self.call("put", state_url, initial, key="contract-state-first")
        conflict = self.call("put", state_url, {"mastered": False, "baseVersion": 0}, key="contract-state-other", client=second_device, status=409)
        self.assertEqual(conflict.data["error"]["fields"]["current"], success.data)
        self.assertEqual(self.call("get", state_url, client=second_device).data, success.data)
        self.call("get", "/api/student/v1/me/preferences/")
        self.call("put", "/api/student/v1/me/preferences/", {"mode": "reciting", "dailyTarget": 5, "baseVersion": 0}, key="contract-pref-first")
        self.call("get", "/api/student/v1/me/statistics/")
        self.call("get", "/api/student/v1/me/favorites/")
        self.revoke_activation()
        favorite = self.call("get", "/api/student/v1/me/favorites/").data["results"][0]
        self.assertIsNone(favorite["question"])
        self.assertEqual(favorite["unavailableReason"], "ENTITLEMENT_REQUIRED")
        self.call("put", state_url, {"favorite": False, "mastered": False, "baseVersion": 1}, key="contract-state-cancel")
        self.assertEqual(self.call("put", state_url, initial, key="contract-state-first").data, success.data)
        self.assertFalse(self.call("get", state_url).data["mastered"])
        replay = self.call("post", "/api/student/v1/activation/redeem/", {"code": codes[0]}, key="contract-redeem-first")
        self.assertFalse(replay.data["entitlement"]["active"])
        self.assertEqual(ActivationCode.objects.filter(state="redeemed").count(), 1)
        self.assertEqual(LearningActivity.objects.count(), 1)
        self.call("post", "/api/student/v1/auth/logout/", {}, status=204)
        self.call("get", "/api/student/v1/config/", status=401)
        self.client.credentials()
        self.call("get", "/api/student/v1/config/")

    def test_fixed_random_revision_and_typed_resume_reauthorize_after_revocation(self):
        self.login()
        codes = self.activation_codes()
        self.call("post", "/api/student/v1/activation/redeem/", {"code": codes[0]}, key="round-contract-redeem")
        context = {"source": "classical", "articleId": "quanxue", "mode": "writing"}
        created = self.call("post", "/api/student/v1/practice/rounds/", context, key="round-contract-first", status=201).data
        old = {question["id"]: question for group in self.groups(created) for question in group["results"]}
        question = Question.objects.get(pk="contract-3")
        question.stem, question.answers = "已重新发布{{0}}。", ["新发布答案"]
        question.save()
        self.publish([question.pk])
        new = {item["id"]: item for group in self.groups(created) for item in group["results"]}
        self.assertEqual(new, old)
        current = self.call("get", "/api/student/v1/questions/contract-3/").data
        self.assertNotEqual(current["revision"], old["contract-3"]["revision"])
        self.call("get", "/api/student/v1/me/resume/")
        group_index = (old["contract-3"]["position"] - 1) // created["pageSize"] + 1
        resumed = self.call("put", "/api/student/v1/me/resume/",
            {**context, "questionId": question.pk, "roundId": created["id"], "groupIndex": group_index, "baseVersion": 0}, key="resume-contract-first")
        self.assertTrue(resumed.data["current"]["valid"])
        self.revoke_activation()
        groups = self.groups(created)
        self.assertNotIn("contract-3", [item["id"] for group in groups for item in group["results"]])
        self.assertEqual(sum(len(group["skipped"]) for group in groups), 1)
        self.assertNotIn(question.answers[0], str(groups))
        resume = self.call("get", "/api/student/v1/me/resume/")
        self.assertFalse(resume.data["valid"])
        self.assertEqual(resume.data["reason"], "ENTITLEMENT_REQUIRED")
        self.assertNotEqual(resume.data["start"]["questionId"], question.pk)

    def test_all_documented_entrypoints_reject_invalid_bearer_and_personal_cookie_only(self):
        document = load_document()
        replacements = {"{id}": "contract-1", "{roundId}": "00000000-0000-4000-8000-000000000001", "{groupIndex}": "1"}
        for path, methods in document["paths"].items():
            actual_path = path
            for key, value in replacements.items():
                actual_path = actual_path.replace(key, value)
            for method, operation in methods.items():
                payload = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("example")
                with self.subTest(path=path, method=method):
                    self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
                    self.call(method, actual_path, payload, status=401)
                    if {} not in operation["security"]:
                        self.client.credentials()
                        self.client.force_login(self.operator)
                        self.call(method, actual_path, payload, status=401)

    def test_every_documented_idempotent_write_requires_key_before_mutation(self):
        self.login()
        document = load_document()
        for path, methods in document["paths"].items():
            for method, operation in methods.items():
                if not operation["x-idempotency"]:
                    continue
                actual_path = path.replace("{id}", "contract-1")
                payload = operation["requestBody"]["content"]["application/json"]["example"]
                with self.subTest(path=path, method=method):
                    response = self.call(method, actual_path, payload, status=400)
                    self.assertEqual(response.data["error"]["code"], "IDEMPOTENCY_KEY_REQUIRED")
