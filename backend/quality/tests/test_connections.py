from django.test import TransactionTestCase

from quality.connections import on_independent_connection


class IndependentConnectionTests(TransactionTestCase):
    def test_worker_exception_reaches_test_caller(self):
        def failing_operation():
            raise ValueError("worker failure must not disappear")

        with self.assertRaisesMessage(ValueError, "worker failure must not disappear"):
            on_independent_connection(failing_operation)

    def test_worker_timeout_error_is_not_replaced_by_cancellation_error(self):
        def failing_operation():
            raise TimeoutError("operation timed out")

        with self.assertRaisesMessage(TimeoutError, "operation timed out"):
            on_independent_connection(failing_operation)
