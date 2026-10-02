from pathlib import Path
import tempfile
import uuid
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase

from operations.backup import backup_database, restore_database


@skipUnless(connection.vendor == "postgresql", "原生 PostgreSQL 恢复只在真实 PostgreSQL 执行。")
class PostgreSQLRestoreTests(TransactionTestCase):
    def test_native_backup_and_restore_into_new_database(self):
        import psycopg
        from psycopg import sql
        config = connection.settings_dict
        target = 'restore_check_' + uuid.uuid4().hex[:16]
        params = {'user':config['USER'], 'password':config['PASSWORD'],
                  'host':config['HOST'], 'port':config['PORT'], 'connect_timeout':10}
        get_user_model().objects.create(username='backup-restoration-probe')
        with psycopg.connect(dbname=config['NAME'], autocommit=True, **params) as control:
            control.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(target)))
            try:
                with tempfile.TemporaryDirectory() as folder:
                    snapshot = backup_database(Path(folder)/'snapshot.dump')
                    restore_database(snapshot, target)
                    with psycopg.connect(dbname=target, **params) as restored:
                        self.assertEqual(restored.execute("SELECT count(*) FROM accounts_user WHERE username='backup-restoration-probe'").fetchone(), (1,))
                        self.assertGreater(restored.execute('SELECT count(*) FROM django_migrations').fetchone()[0], 0)
                    self.assertEqual(get_user_model().objects.filter(username='backup-restoration-probe').count(), 1)
                    from django.core.management.base import CommandError
                    with self.assertRaises(CommandError): restore_database(snapshot, target)
            finally:
                # Only our checked, random isolated test target is removed.
                control.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(target)))
