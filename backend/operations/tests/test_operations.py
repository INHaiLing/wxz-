import io
import json
import logging
from pathlib import Path
import sqlite3
import tempfile
from contextlib import closing
from unittest.mock import patch

from django.core.management.base import CommandError
from django.db import DatabaseError
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from operations.api import readyz
from operations.backup import backup_database, restore_database
from operations.logging import RequestLoggingMiddleware, SafeJSONFormatter


class OperationsTests(SimpleTestCase):
    def test_readiness_does_not_expose_database_error(self):
        request = RequestFactory().get('/readyz')
        with patch('operations.api.connection') as connection:
            connection.cursor.side_effect = DatabaseError('password=SECRET')
            response = readyz(request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content), {'status': 'unavailable'})

    def test_request_log_excludes_input_and_matches_response_id(self):
        request = RequestFactory().post('/private?token=SECRET', {'code': 'RAWCODE'}, HTTP_AUTHORIZATION='Bearer SECRET')
        with self.assertLogs('operations.http', level='INFO') as logs:
            response = RequestLoggingMiddleware(lambda r: HttpResponse('ok'))(request)
        record = json.loads(SafeJSONFormatter().format(logs.records[0]))
        self.assertEqual(record['requestId'], response['X-Request-ID'])
        self.assertNotIn('SECRET', json.dumps(record))
        self.assertNotIn('RAWCODE', json.dumps(record))

    def test_formatter_omits_exception_values(self):
        record = logging.LogRecord('django.request', logging.ERROR, '', 1, 'secret %s', ('PASSWORD',), None)
        self.assertNotIn('PASSWORD', SafeJSONFormatter().format(record))

    def test_sqlite_roundtrip_and_refuse_overwrite_live_database(self):
        with tempfile.TemporaryDirectory() as folder:
            live = Path(folder)/'live.sqlite3'
            with closing(sqlite3.connect(live)) as db:
                db.execute('CREATE TABLE django_migrations (id integer primary key)')
                db.execute('INSERT INTO django_migrations VALUES(7)')
                db.execute('CREATE TABLE rights (id integer primary key, status text)')
                db.execute("INSERT INTO rights VALUES(1, 'revoked')")
                db.commit()
            config = {'ENGINE':'django.db.backends.sqlite3', 'NAME': str(live)}
            with patch('operations.backup.connections') as connections:
                connections.__getitem__.return_value.settings_dict = config
                backup = backup_database(Path(folder)/'snapshot.sqlite3')
                target = restore_database(backup, Path(folder)/'restored.sqlite3')
                with closing(sqlite3.connect(target)) as db:
                    self.assertEqual(db.execute('SELECT status FROM rights').fetchone(), ('revoked',))
                for fn, args in ((backup_database,(backup,)), (restore_database,(backup,live)), (restore_database,(backup,target))):
                    with self.assertRaises(CommandError): fn(*args)
            with closing(sqlite3.connect(live)) as db:
                self.assertEqual(db.execute('SELECT status FROM rights').fetchone(), ('revoked',))

    def test_damaged_sqlite_restore_does_not_create_target(self):
        with tempfile.TemporaryDirectory() as folder:
            bad, target = Path(folder)/'bad', Path(folder)/'target'
            bad.write_bytes(b'not sqlite')
            with patch('operations.backup.connections') as connections:
                connections.__getitem__.return_value.settings_dict = {'ENGINE':'django.db.backends.sqlite3','NAME': str(Path(folder)/'live')}
                with self.assertRaises(CommandError): restore_database(bad,target)
            self.assertFalse(target.exists())

    def test_missing_pg_tool_and_password_not_in_arguments(self):
        from operations.backup import _run
        with patch('operations.backup.shutil.which',return_value=None):
            with self.assertRaises(CommandError): _run(['pg_dump'], {'PASSWORD':'SECRET'})
        with patch('operations.backup.shutil.which',return_value='/bin/pg_dump'), patch('operations.backup.subprocess.run') as run:
            _run(['pg_dump','--dbname','db'], {'PASSWORD':'SECRET'})
        self.assertNotIn('SECRET', repr(run.call_args.args))
        self.assertEqual(run.call_args.kwargs['env']['PGPASSWORD'], 'SECRET')
