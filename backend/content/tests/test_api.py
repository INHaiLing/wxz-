from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from content.models import Article, Category, Question, QuestionRevision


class ContentAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(id="pre-qin", name="先秦文学")
        cls.other_category = Category.objects.create(id="tang", name="唐代文学")
        cls.article = Article.objects.create(id="quanxue", title="劝学")
        cls.other_article = Article.objects.create(id="shishuo", title="师说")
        cls.staff = get_user_model().objects.create_user(username="api-reader", is_staff=True)
        cls.staff.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="content",
                codename__in=("view_category", "view_article", "view_question"),
            )
        )
        cls.student = get_user_model().objects.create_user(username="api-student")
        cls.student.user_permissions.set(cls.staff.user_permissions.all())
        cls.no_permission = get_user_model().objects.create_user(
            username="api-no-permission", is_staff=True
        )
        cls.question = cls.make_question("lit-shijing", published=True)
        cls.draft = cls.make_question("draft-secret", published=False, answers=["草稿机密"])
        cls.classical = cls.make_question(
            "word-rou", published=True, source="classical", category=None,
            article=cls.article, type="word", stem="輮的意思是{{0}}。", answers=["使弯曲"],
        )

    @classmethod
    def make_question(cls, question_id, *, published=True, **overrides):
        values = {
            "source": "literature", "category": cls.category, "article": None,
            "type": "fact", "tag": "文学常识", "stem": "《诗经》收录{{0}}篇。",
            "answers": ["305"], "sort_order": 0,
        }
        values.update(overrides)
        question = Question.objects.create(id=question_id, **values)
        if published:
            revision = QuestionRevision.objects.create(question=question, version=1, **values)
            question.published_revision = revision
            question.is_published = True
            question.save()
        return question

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.staff)

    def test_all_endpoints_require_staff_and_specific_permission(self):
        endpoints = (
            "/api/v1/categories/", "/api/v1/articles/", "/api/v1/questions/",
            f"/api/v1/questions/{self.question.pk}/",
        )
        for user in (None, self.student, self.no_permission):
            client = APIClient()
            if user is not None:
                client.force_login(user)
            for endpoint in endpoints:
                with self.subTest(user=getattr(user, "username", "anonymous"), endpoint=endpoint):
                    response = client.get(endpoint)
                    self.assertEqual(response.status_code, 403)
                    self.assertNotIn("305", response.content.decode())

        self.no_permission.user_permissions.add(
            Permission.objects.get(content_type__app_label="content", codename="view_category")
        )
        client = APIClient()
        client.force_login(self.no_permission)
        self.assertEqual(client.get("/api/v1/categories/").status_code, 200)
        self.assertEqual(client.get("/api/v1/questions/").status_code, 403)
        metadata = client.get("/api/v1/categories/").json()
        self.assertNotIn("answers", str(metadata))
        self.assertNotIn("stem", str(metadata))

    def test_inactive_staff_cannot_read_and_bearer_is_not_session_auth(self):
        self.staff.is_active = False
        self.staff.save(update_fields=["is_active"])
        self.assertEqual(self.client.get("/api/v1/questions/").status_code, 403)
        client = APIClient()
        self.assertEqual(
            client.get("/api/v1/questions/", HTTP_AUTHORIZATION="Bearer arbitrary").status_code,
            403,
        )

    def test_list_and_detail_expose_only_published_snapshot(self):
        response = self.client.get("/api/v1/questions/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload), {"count", "next", "previous", "results"})
        self.assertEqual(payload["count"], 2)
        self.assertNotIn("草稿机密", response.content.decode())
        self.assertEqual(self.client.get(f"/api/v1/questions/{self.draft.pk}/").status_code, 404)
        item = self.client.get(f"/api/v1/questions/{self.question.pk}/").json()
        self.assertEqual(item, {
            "id": self.question.pk, "source": "literature", "categoryId": "pre-qin",
            "articleId": None, "type": "fact", "tag": "文学常识",
            "stem": "《诗经》收录{{0}}篇。", "answers": ["305"],
            "sortOrder": 0, "revision": 1,
        })

    def test_draft_edits_do_not_change_published_content_filters_or_sort(self):
        self.question.source = "classical"
        self.question.category = None
        self.question.article = self.other_article
        self.question.type = "translation"
        self.question.stem = "待发布{{0}}"
        self.question.answers = ["新答案机密"]
        self.question.tag = "新标签"
        self.question.sort_order = 999
        self.question.save()
        self.category.is_active = True
        self.category.save()
        query = {"source": "literature", "categoryId": "pre-qin", "type": "fact"}
        response = self.client.get("/api/v1/questions/", query)
        self.assertEqual(response.status_code, 200)
        item = response.json()["results"][0]
        self.assertEqual(item["id"], self.question.pk)
        self.assertEqual(item["answers"], ["305"])
        self.assertEqual(item["sortOrder"], 0)
        self.assertEqual(item["tag"], "文学常识")
        self.assertNotIn("新答案机密", response.content.decode())
        self.assertEqual(
            self.client.get("/api/v1/questions/", {"articleId": "shishuo"}).json()["count"], 0
        )
        self.assertEqual(
            self.client.get("/api/v1/questions/").json()["results"][0]["id"], self.question.pk
        )

    def test_draft_category_and_article_changes_keep_published_catalog(self):
        self.question.category = self.other_category
        self.question.save()
        self.classical.article = self.other_article
        self.classical.save()
        self.assertEqual(
            self.client.get("/api/v1/questions/", {"categoryId": "pre-qin"}).json()["count"], 1
        )
        self.assertEqual(
            self.client.get("/api/v1/questions/", {"categoryId": "tang"}).json()["count"], 0
        )
        self.assertEqual(
            self.client.get("/api/v1/questions/", {"articleId": "quanxue"}).json()["count"], 1
        )
        self.assertEqual(
            self.client.get("/api/v1/questions/", {"articleId": "shishuo"}).json()["count"], 0
        )

    def test_new_publication_and_unpublish_control_visibility(self):
        revision = QuestionRevision.objects.create(
            question=self.question, version=2, source="literature", category=self.other_category,
            article=None, type="fact", tag="新发布", stem="新内容{{0}}", answers=["已发布答案"],
            sort_order=4,
        )
        self.question.published_revision = revision
        self.question.save()
        item = self.client.get(f"/api/v1/questions/{self.question.pk}/").json()
        self.assertEqual(item["revision"], 2)
        self.assertEqual(item["categoryId"], "tang")
        self.assertEqual(item["answers"], ["已发布答案"])
        self.assertEqual(
            self.client.get("/api/v1/questions/", {"categoryId": "pre-qin"}).json()["count"], 0
        )
        self.question.is_published = False
        self.question.save()
        self.assertEqual(self.client.get(f"/api/v1/questions/{self.question.pk}/").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/questions/").json()["count"], 1)

    def test_inactive_published_catalog_hides_questions_in_list_and_detail(self):
        # Normal model writes forbid this state. The API must also fail closed
        # for legacy/inconsistent records created outside the model workflow.
        Category.objects.filter(pk=self.category.pk).update(is_active=False)
        Article.objects.filter(pk=self.article.pk).update(is_active=False)
        self.assertEqual(self.client.get("/api/v1/questions/").json()["count"], 0)
        for question in (self.question, self.classical):
            self.assertEqual(self.client.get(f"/api/v1/questions/{question.pk}/").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/categories/").json()["count"], 1)
        self.assertEqual(self.client.get("/api/v1/articles/").json()["count"], 1)

    def test_unpublished_or_foreign_revision_is_never_exposed(self):
        # Bypass model validation to emulate inconsistent persisted data.
        Question.objects.filter(pk=self.question.pk).update(
            published_revision=self.classical.published_revision
        )
        Question.objects.filter(pk=self.draft.pk).update(is_published=True)
        response = self.client.get("/api/v1/questions/")
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["results"][0]["id"], self.classical.pk)
        for question in (self.question, self.draft):
            self.assertEqual(self.client.get(f"/api/v1/questions/{question.pk}/").status_code, 404)

    def test_known_filters_and_absent_ids_return_narrowed_or_empty_results(self):
        response = self.client.get("/api/v1/questions/", {
            "source": "classical", "articleId": "quanxue", "type": "word"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["results"]], [self.classical.pk])
        for query in ({"categoryId": "absent"}, {"articleId": "absent"}, {
            "source": "literature", "articleId": "quanxue"
        }):
            with self.subTest(query=query):
                response = self.client.get("/api/v1/questions/", query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["count"], 0)
        self.assertEqual(self.client.get("/api/v1/questions/absent/").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/questions/not.valid/").status_code, 404)

    def test_invalid_unknown_empty_and_repeated_parameters_are_rejected(self):
        invalid_queries = (
            "categoryID=pre-qin", "source=unknown", "type=essay", "source=", "type=",
            "categoryId=", "articleId=", "categoryId=bad.id", "articleId=bad%2Fid",
            "source=literature&source=classical", "page=1&page=2", "page=0", "page=-1",
            "page=last", "page=1.0", "page_size=0", "page_size=101", "page_size=nope",
            "page_size=", "ordering=answers", "format=json",
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                response = self.client.get(f"/api/v1/questions/?{query}")
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("305", response.content.decode())
        for endpoint in ("categories", "articles"):
            self.assertEqual(self.client.get(f"/api/v1/{endpoint}/?source=literature").status_code, 400)
        self.assertEqual(
            self.client.get(f"/api/v1/questions/{self.question.pk}/?typo=yes").status_code, 400
        )

    def test_pagination_defaults_maximum_and_stable_order(self):
        for index in range(103):
            self.make_question(f"extra-{index:03d}", sort_order=2)
        first = self.client.get("/api/v1/questions/").json()
        self.assertEqual(first["count"], 105)
        self.assertEqual(len(first["results"]), 20)
        self.assertIsNone(first["previous"])
        self.assertIsNotNone(first["next"])
        large = self.client.get("/api/v1/questions/", {"page_size": "100"}).json()
        self.assertEqual(len(large["results"]), 100)
        last = self.client.get("/api/v1/questions/", {"page_size": "100", "page": "2"}).json()
        self.assertEqual(len(last["results"]), 5)
        self.assertIsNone(last["next"])
        self.assertIsNotNone(last["previous"])
        merged = large["results"] + last["results"]
        self.assertEqual(len({item["id"] for item in merged}), 105)
        self.assertEqual(
            [(item["sortOrder"], item["id"]) for item in merged],
            sorted((item["sortOrder"], item["id"]) for item in merged),
        )
        self.assertEqual(
            self.client.get("/api/v1/questions/", {"page_size": "100", "page": "3"}).status_code,
            404,
        )

    def test_catalog_pagination_and_order_are_consistent(self):
        for index in range(21):
            Category.objects.create(id=f"category-{index:02d}", name=f"分类{index}", sort_order=1)
            Article.objects.create(id=f"article-{index:02d}", title=f"篇目{index}", sort_order=1)
        for endpoint in ("categories", "articles"):
            with self.subTest(endpoint=endpoint):
                result = self.client.get(f"/api/v1/{endpoint}/").json()
                self.assertEqual(result["count"], 23)
                self.assertEqual(len(result["results"]), 20)
                self.assertEqual(set(result), {"count", "next", "previous", "results"})
                self.assertEqual(
                    [(row["sortOrder"], row["id"]) for row in result["results"]],
                    sorted((row["sortOrder"], row["id"]) for row in result["results"]),
                )

    def test_write_methods_are_not_available(self):
        before_count = Question.objects.count()
        for endpoint in (
            "/api/v1/categories/", "/api/v1/articles/", "/api/v1/questions/",
            f"/api/v1/questions/{self.question.pk}/",
        ):
            for method in ("post", "put", "patch", "delete"):
                with self.subTest(endpoint=endpoint, method=method):
                    response = getattr(self.client, method)(endpoint, {"answers": ["tampered"]}, format="json")
                    self.assertEqual(response.status_code, 405)
        self.assertEqual(Question.objects.count(), before_count)
        self.question.refresh_from_db()
        self.assertEqual(self.question.answers, ["305"])

    def test_healthz_is_anonymous_and_minimal(self):
        response = APIClient().get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(APIClient().post("/healthz").status_code, 405)
