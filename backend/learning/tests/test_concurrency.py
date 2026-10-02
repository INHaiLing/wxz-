from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, skipUnlessDBFeature

from common.errors import BusinessError
from common.models import IdempotencyRecord
from learning.models import LearningActivity, LearningPreference, QuestionState
from learning.services import update_preferences, update_question_state
from quality.connections import on_independent_connection
from .fixtures import build_fixture


@skipUnlessDBFeature("has_select_for_update")
class LearningConcurrencyTests(TransactionTestCase):
    def setUp(self):
        build_fixture(self)

    def compete(self, operation):
        barrier = Barrier(2)

        def worker(index):
            user = get_user_model().objects.get(pk=self.student.pk)
            barrier.wait(timeout=5)
            try:
                return operation(user, index)
            except BusinessError as error:
                return str(error.detail["error"]["code"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(on_independent_connection, lambda i=index: worker(i)) for index in (0, 1)]
            return [future.result(timeout=20) for future in futures]

    def test_two_devices_same_version_only_one_write_and_activity(self):
        results = self.compete(lambda user, index: update_question_state(
            user, self.first.pk, {"mastered": True, "baseVersion": 0}, f"device-state-{index:03}",
        ))
        self.assertEqual(results.count("VERSION_CONFLICT"), 1)
        self.assertEqual(QuestionState.objects.get().version, 1)
        self.assertEqual(LearningActivity.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_same_key_concurrent_retry_returns_identical_historical_success(self):
        results = self.compete(lambda user, index: update_question_state(
            user, self.first.pk, {"mastered": True, "favorite": True, "baseVersion": 0}, "same-learning-key-001",
        ))
        self.assertEqual(results[0], results[1])
        self.assertEqual(QuestionState.objects.get().version, 1)
        self.assertEqual(LearningActivity.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_preference_version_serializes_two_device_updates(self):
        results = self.compete(lambda user, index: update_preferences(
            user, {"dailyTarget": index + 1, "baseVersion": 0}, f"device-preference-{index:03}",
        ))
        self.assertEqual(results.count("VERSION_CONFLICT"), 1)
        self.assertEqual(LearningPreference.objects.get().version, 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)
