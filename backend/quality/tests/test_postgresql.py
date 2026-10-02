from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import OperationalError, connection, transaction
from django.test import TransactionTestCase

from quality.connections import on_independent_connection


def backend_pid():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


@skipUnless(connection.vendor == "postgresql", "Requires real PostgreSQL; SQLite is not concurrency evidence.")
class PostgreSQLTransactionFoundationTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="transaction-test", last_name="original")
        self.main_pid = backend_pid()

    def read_name(self):
        return backend_pid(), get_user_model().objects.get(pk=self.user.pk).last_name

    def try_lock(self):
        pid = backend_pid()
        try:
            with transaction.atomic():
                get_user_model().objects.select_for_update(nowait=True).get(pk=self.user.pk)
            return pid, None
        except OperationalError as error:
            return pid, error.__cause__.sqlstate

    def test_row_lock_rejects_second_connection_until_commit(self):
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=self.user.pk)
            pid, sqlstate = on_independent_connection(self.try_lock)
            self.assertNotEqual(pid, self.main_pid)
            self.assertEqual(sqlstate, "55P03")
        pid, sqlstate = on_independent_connection(self.try_lock)
        self.assertNotEqual(pid, self.main_pid)
        self.assertIsNone(sqlstate)

    def test_other_connection_sees_change_only_after_commit(self):
        with transaction.atomic():
            get_user_model().objects.filter(pk=self.user.pk).update(last_name="committed")
            pid, name = on_independent_connection(self.read_name)
            self.assertNotEqual(pid, self.main_pid)
            self.assertEqual(name, "original")
        pid, name = on_independent_connection(self.read_name)
        self.assertNotEqual(pid, self.main_pid)
        self.assertEqual(name, "committed")

    def test_rollback_leaves_no_partial_update(self):
        with self.assertRaisesMessage(ValueError, "force rollback"):
            with transaction.atomic():
                get_user_model().objects.filter(pk=self.user.pk).update(last_name="rolled-back")
                pid, name = on_independent_connection(self.read_name)
                self.assertNotEqual(pid, self.main_pid)
                self.assertEqual(name, "original")
                raise ValueError("force rollback")
        pid, name = on_independent_connection(self.read_name)
        self.assertNotEqual(pid, self.main_pid)
        self.assertEqual(name, "original")
