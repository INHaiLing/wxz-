import base64
import os
from pathlib import Path
import subprocess
import sys

from django.test import SimpleTestCase


class ProductionConfigurationTests(SimpleTestCase):
    def config_process(self, **overrides):
        env=dict(os.environ)
        env.update({
            'DJANGO_DEBUG':'false','DJANGO_SECRET_KEY':'test-only-private-disposable-'+'x'*60,
            'DJANGO_ALLOWED_HOSTS':'test.example.invalid','DJANGO_CSRF_TRUSTED_ORIGINS':'https://test.example.invalid',
            'DB_ENGINE':'postgresql','DB_NAME':'test_config','DB_PASSWORD':'disposable-test-password',
            'STUDENT_SESSION_ENCRYPTION_KEYS':base64.urlsafe_b64encode(b'x'*32).decode(),
            'VIRTUAL_PAYMENT_ENABLED':'false','WECHAT_APP_SECRET':'',
        })
        env.update(overrides)
        return subprocess.run([sys.executable,'-c','import config.production_settings as settings; print(settings.MEDIA_ROOT)'],
                              cwd=Path(__file__).resolve().parents[2],env=env,capture_output=True,timeout=15)

    def test_valid_configuration_imports_without_external_connections(self):
        result = self.config_process()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode().strip().replace("\\", "/"), "/var/lib/chinese-study/media")

    def test_media_root_uses_explicit_durable_directory(self):
        result = self.config_process(DJANGO_MEDIA_ROOT="/srv/chinese-study/media")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode().strip().replace("\\", "/"), "/srv/chinese-study/media")

    def test_invalid_critical_settings_refuse_startup(self):
        cases=[{'DJANGO_DEBUG':'true'},{'DB_ENGINE':'sqlite'}, {'DJANGO_ALLOWED_HOSTS':'*'},
               {'STUDENT_SESSION_ENCRYPTION_KEYS':''}, {'STUDENT_SESSION_ENCRYPTION_KEYS':'invalid'},
               {'DJANGO_CSRF_TRUSTED_ORIGINS':'http://test.example.invalid'}, {'DB_PASSWORD':''}]
        for case in cases:
            with self.subTest(setting=tuple(case)):
                self.assertNotEqual(self.config_process(**case).returncode,0)
