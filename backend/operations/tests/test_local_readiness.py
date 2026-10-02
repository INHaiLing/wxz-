import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, override_settings

from operations.local_readiness import CONTENT_ROLES, collect_readiness
from operations.management.commands.setup_business_roles import ROLES
from scripts.local_configuration import configure, parse_env
from .test_local_configuration import APP, OFFER, SECRETS, isolate_process, sqlite_database


class LocalReadinessTests(SimpleTestCase):
    def setUp(self):
        isolate_process(self)
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        (self.folder / ".env.example").write_text("DJANGO_SECRET_KEY=replace-with-generated-secret-at-least-32-characters\n", encoding="utf-8")
        configure(self.folder, APP, OFFER, dict.fromkeys(("filing", "virtualPayment", "appleIap", "developerMember"), True),
                  fill_missing=True, prompt=lambda name: SECRETS[name])
        self.values = parse_env((self.folder / ".env").read_bytes())[2]
        self.database = self.folder / "db.sqlite3"
        with sqlite_database(self.database) as db:
            db.executescript("""
                CREATE TABLE django_migrations (app TEXT, name TEXT);
                CREATE TABLE accounts_user (is_superuser BOOLEAN, is_active BOOLEAN);
                INSERT INTO accounts_user VALUES (1,1);
                CREATE TABLE accounts_wechatidentity (app_id TEXT, encrypted_session_key TEXT);
                CREATE TABLE accounts_studentsession (encrypted_session_key TEXT);
                CREATE TABLE auth_group (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE auth_group_permissions (group_id INTEGER, permission_id INTEGER);
                CREATE TABLE auth_permission (id INTEGER PRIMARY KEY, content_type_id INTEGER, codename TEXT);
                CREATE TABLE django_content_type (id INTEGER PRIMARY KEY, app_label TEXT, model TEXT);
                CREATE TABLE entitlements_product (id TEXT, is_active BOOLEAN, price_fen INTEGER, platform_product_id TEXT, platform_sync_state TEXT, platform_synced_price_fen INTEGER);
                INSERT INTO entitlements_product VALUES ('all-chinese',1,1000,'disposable-mapping','synced',1000);
            """)
            db.executemany("INSERT INTO django_migrations VALUES (?,?)", list(MigrationLoader(None).disk_migrations))
            permission_id = 0
            for group_id, (name, permissions) in enumerate({**ROLES, **CONTENT_ROLES}.items(), 1):
                db.execute("INSERT INTO auth_group VALUES (?,?)", (group_id, name))
                for app, model, code in permissions:
                    permission_id += 1
                    db.execute("INSERT INTO django_content_type VALUES (?,?,?)", (permission_id, app, model))
                    db.execute("INSERT INTO auth_permission VALUES (?,?,?)", (permission_id, permission_id, code))
                    db.execute("INSERT INTO auth_group_permissions VALUES (?,?)", (group_id, permission_id))
        self.overrides = {
            "WECHAT_APP_ID": APP, "VIRTUAL_PAYMENT_OFFER_ID": OFFER,
            "STUDENT_SESSION_ENCRYPTION_KEYS": (self.values["STUDENT_SESSION_ENCRYPTION_KEYS"],),
            "VIRTUAL_PAYMENT_ENABLED": False, "VIRTUAL_PAYMENT_ANDROID_ENABLED": False, "VIRTUAL_PAYMENT_IOS_ENABLED": False,
            "VIRTUAL_PAYMENT_ENV": 0, **SECRETS,
            "DATABASES": {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(self.database)}},
        }

    def report(self, **overrides):
        with override_settings(**{**self.overrides, **overrides}), patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No network allowed")):
            return collect_readiness(self.folder)

    def status(self, report):
        return {check["code"]: check["status"] for check in report["checks"]}

    def test_complete_local_structure_is_read_only_and_external_checks_remain_pending(self):
        paths = (self.database, self.folder / ".env", self.folder / ".local/wechat-account.json")
        before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        report = self.report()
        states = self.status(report)
        self.assertTrue(all(state == "pending" for code, state in states.items() if code.startswith("EXTERNAL_")))
        self.assertFalse(any(state in ("missing", "blocked") for state in states.values()), report)
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])
        serialized = json.dumps(report, ensure_ascii=False)
        for private in tuple(SECRETS.values()) + (self.values["STUDENT_SESSION_ENCRYPTION_KEYS"], APP, OFFER, "disposable-mapping"):
            self.assertNotIn(private, serialized)
        self.assertEqual(set(report), {"checks"})

    def test_missing_migrations_admin_and_product_are_safe_states(self):
        with sqlite_database(self.database) as db:
            db.execute("DELETE FROM django_migrations")
            db.execute("DELETE FROM accounts_user")
            db.execute("DELETE FROM entitlements_product")
        states = self.status(self.report(WECHAT_APP_SECRET=""))
        for code in ("MIGRATIONS", "ADMINISTRATOR", "PRODUCT", "PRODUCT_MAPPING", "SECRET_WECHAT_APP_SECRET"):
            self.assertEqual(states[code], "missing")
        self.assertEqual(states["PRODUCT_SYNC"], "pending")

    def test_empty_content_role_or_missing_required_permission_is_not_ready(self):
        with sqlite_database(self.database) as db:
            db.execute("DELETE FROM auth_group_permissions WHERE group_id=(SELECT id FROM auth_group WHERE name='题库发布')")
        self.assertEqual(self.status(self.report())["BUSINESS_ROLES"], "missing")

    def test_file_profile_and_existing_identity_mismatch_are_blocked_without_identifiers(self):
        report = self.report(WECHAT_APP_ID="wx0000000000000002")
        self.assertEqual(self.status(report)["ACCOUNT_PROFILE"], "blocked")
        with sqlite_database(self.database) as db:
            db.execute("INSERT INTO accounts_wechatidentity VALUES ('wx0000000000000002','historical-encrypted-key')")
            db.execute("INSERT INTO accounts_studentsession VALUES ('historical-encrypted-session')")
        report = self.report()
        self.assertEqual(self.status(report)["ACCOUNT_DATA"], "blocked")
        self.assertNotIn("wx0000000000000002", json.dumps(report))

    def test_old_encrypted_data_without_ring_cannot_be_misreported_as_new_configuration(self):
        with sqlite_database(self.database) as db:
            db.execute("INSERT INTO accounts_studentsession VALUES ('private-ciphertext')")
        report = self.report(STUDENT_SESSION_ENCRYPTION_KEYS=())
        states = self.status(report)
        self.assertEqual(states["SESSION_ENCRYPTION"], "missing")
        self.assertEqual(states["ENCRYPTION_KEY_RECOVERY_REQUIRED"], "blocked")

    def test_unreachable_database_does_not_expose_exception_and_missing_file_is_not_created(self):
        with patch("operations.local_readiness.safe_probe", side_effect=RuntimeError("password=PRIVATE")):
            report = self.report()
        self.assertEqual(self.status(report)["DATABASE_DIAGNOSIS"], "blocked")
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.database.unlink()
        self.assertEqual(self.status(self.report())["DATABASE"], "missing")
        self.assertFalse(self.database.exists())

    def test_sales_and_extra_role_permissions_block_readiness_without_repairing_data(self):
        with sqlite_database(self.database) as db:
            db.execute("INSERT INTO django_content_type VALUES (99999,'accounts','user')")
            db.execute("INSERT INTO auth_permission VALUES (99999,99999,'change_user')")
            db.execute("INSERT INTO auth_group_permissions VALUES (1,99999)")
        report = self.report(VIRTUAL_PAYMENT_ENABLED=True)
        self.assertEqual(self.status(report)["SALES_DISABLED"], "blocked")
        self.assertEqual(self.status(report)["BUSINESS_ROLES"], "blocked")

    def test_bad_envelope_and_duplicate_configuration_report_safe_failures(self):
        report = self.report(VIRTUAL_PAYMENT_CALLBACK_AES_KEY="PRIVATE_INVALID_AES")
        self.assertEqual(self.status(report)["SECRET_VIRTUAL_PAYMENT_CALLBACK_AES_KEY"], "blocked")
        self.assertNotIn("PRIVATE_INVALID_AES", json.dumps(report))
        with (self.folder / ".env").open("a") as handle:
            handle.write("WECHAT_APP_ID=ambiguous\n")
        self.assertEqual(self.status(self.report())["ENV_DUPLICATE"], "blocked")
