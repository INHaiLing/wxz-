from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import TransactionTestCase

from common.errors import BusinessError
from common.models import IdempotencyRecord
from content.models import Article, LearningConfiguration, Question, QuestionRevision
from practice.models import PracticeRound, ResumePosition
from practice.services import create_round, save_resume


@skipUnless(connection.vendor == "postgresql", "Requires real PostgreSQL independent transactions")
class PracticePostgreSQLTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="p08-pg-student")
        article = Article.objects.create(id="p08-pg-article", title="并发测试")
        LearningConfiguration.objects.create(page_size=2)
        for index in range(2):
            question = Question.objects.create(id=f"p08-pg-{index}", source="classical", article=article,
                                                type="word", stem="测试{{0}}", answers=["答案"], sort_order=index)
            revision = QuestionRevision.objects.create(question=question, version=1, **question.content_payload())
            question.is_published, question.published_revision = True, revision
            question.save()
        self.payload = {"source": "classical", "articleId": article.pk, "categoryId": None, "type": None, "mode": "writing"}

    def parallel(self, operation):
        barrier = Barrier(2, timeout=5)

        def run(index):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                barrier.wait()
                try:
                    result = operation(index)
                except BusinessError as error:
                    result = error.code
                return pid, result
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run, index) for index in range(2)]
            result = [future.result(timeout=15) for future in futures]
        self.assertNotEqual(result[0][0], result[1][0])
        return [value for _, value in result]

    def test_simultaneous_same_key_creates_one_fixed_round(self):
        results = self.parallel(lambda index: create_round(self.user, self.payload, "p08-pg-same-key"))
        self.assertEqual(results[0], results[1])
        self.assertEqual(PracticeRound.objects.count(), 1)
        self.assertEqual(PracticeRound.objects.get().items.count(), 2)
        self.assertEqual(IdempotencyRecord.objects.filter(operation="practice.round.create").count(), 1)

    def test_simultaneous_same_version_produces_one_write_and_one_conflict(self):
        payload = {**self.payload, "questionId": "p08-pg-0", "roundId": None, "groupIndex": None, "baseVersion": 0}
        results = self.parallel(lambda index: save_resume(self.user, payload, f"p08-pg-resume-{index}"))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn("VERSION_CONFLICT", results)
        self.assertEqual(ResumePosition.objects.get().version, 1)
        self.assertEqual(IdempotencyRecord.objects.filter(operation="practice.resume.save").count(), 1)
