import base64
from contextlib import closing, contextmanager, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import SimpleTestCase

from scripts.local_configuration import (LocalConfigurationError, SECRET_FIELDS, configure,
    hidden_secret, parse_env, read_private)


APP = "wx0000000000000001"
OFFER = "1234567890"
SECRETS = dict.fromkeys(SECRET_FIELDS, "disposable-only-$'\"\\-value")
SECRETS["VIRTUAL_PAYMENT_CALLBACK_AES_KEY"] = base64.b64encode(b"x" * 32).decode().rstrip("=")


@contextmanager
def sqlite_database(path):
    with closing(sqlite3.connect(path)) as connection, connection:
        yield connection


def isolate_process(test):
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith(("WECHAT_", "VIRTUAL_PAYMENT_", "DB_"))
                   and name not in ("SQLITE_PATH", "STUDENT_SESSION_ENCRYPTION_KEYS")}
    isolated = patch.dict(os.environ, environment, clear=True)
    isolated.start()
    test.addCleanup(isolated.stop)


class LocalConfigurationTests(SimpleTestCase):
    def setUp(self):
        isolate_process(self)
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        (self.folder / ".env.example").write_text(
            "DJANGO_SECRET_KEY=replace-with-generated-secret-at-least-32-characters\n"
            "DB_ENGINE=sqlite\nSQLITE_PATH=db.sqlite3\nVIRTUAL_PAYMENT_ENABLED=false\n"
            "VIRTUAL_PAYMENT_ANDROID_ENABLED=false\nVIRTUAL_PAYMENT_IOS_ENABLED=false\n", encoding="utf-8")

    def prepared(self, **options):
        return configure(self.folder, APP, OFFER, **options)

    def values(self):
        return parse_env((self.folder / ".env").read_bytes())[2]

    def seed_encrypted(self, table="accounts_studentsession", column="encrypted_session_key", ciphertext="old-ciphertext"):
        with sqlite_database(self.folder / "db.sqlite3") as connection:
            extra = ', app_id TEXT' if table in ("accounts_wechatidentity", "payments_accesstokencache") else ''
            connection.execute(f'CREATE TABLE "{table}" ("{column}" TEXT{extra})')
            connection.execute(f'INSERT INTO "{table}" ("{column}") VALUES (?)', (ciphertext,))

    def test_missing_fields_are_filled_and_repeat_preserves_keys_secrets_and_unrelated_values(self):
        original_key = Fernet.generate_key().decode()
        (self.folder / ".env").write_text("DJANGO_SECRET_KEY=old-private-django-value-for-local-tests\n"
            f"STUDENT_SESSION_ENCRYPTION_KEYS={original_key}\nUNRELATED='keep ${{unchanged}}'\n", encoding="utf-8")
        self.assertEqual(self.prepared(fill_missing=True, prompt=lambda name: SECRETS[name]), [])
        before = (self.folder / ".env").read_bytes()
        self.assertEqual(self.prepared(fill_missing=True, prompt=lambda _: self.fail("Must not prompt filled secrets")), [])
        self.assertEqual(before, (self.folder / ".env").read_bytes())
        values = self.values()
        self.assertEqual(values["STUDENT_SESSION_ENCRYPTION_KEYS"], original_key)
        self.assertEqual(values["DJANGO_SECRET_KEY"], "old-private-django-value-for-local-tests")
        self.assertEqual(values["UNRELATED"], "keep ${unchanged}")
        self.assertEqual(values["WECHAT_APP_SECRET"], SECRETS["WECHAT_APP_SECRET"])
        profile = json.loads((self.folder / ".local/wechat-account.json").read_bytes())
        self.assertEqual(set(profile), {"schemaVersion", "appId", "offerId", "platformConfirmed"})
        self.assertNotIn("PRIVATE", json.dumps(profile))
        for name in ("VIRTUAL_PAYMENT_ENABLED", "VIRTUAL_PAYMENT_ANDROID_ENABLED", "VIRTUAL_PAYMENT_IOS_ENABLED"):
            self.assertEqual(values[name], "false")
        if os.name != "nt":
            self.assertEqual((self.folder / ".env").stat().st_mode & 0o777, 0o600)

    def test_identifiers_only_saves_profile_and_reports_missing_secrets_without_creating_database(self):
        self.assertEqual(set(self.prepared()), set(SECRET_FIELDS))
        self.assertTrue(Fernet(self.values()["STUDENT_SESSION_ENCRYPTION_KEYS"]))
        self.assertFalse((self.folder / "db.sqlite3").exists())

    def test_all_secret_staging_and_profile_recovery_files_stay_in_ignored_private_directory(self):
        from scripts.local_configuration import private_temp
        private_folder = self.folder / ".local"
        targets = (self.folder / ".env", private_folder / "wechat-account.json")
        staged = []
        try:
            for target in targets:
                file = private_temp(target, b"disposable-private-staging-probe")
                staged.append(file)
                self.assertEqual(file.parent, private_folder)
                self.assertTrue(file.name.startswith(".config-"))
                self.assertEqual(file.read_bytes(), b"disposable-private-staging-probe")
                if os.name != "nt":
                    self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            if os.name != "nt":
                self.assertEqual(private_folder.stat().st_mode & 0o777, 0o700)
            project = Path(__file__).resolve().parents[3]
            result = subprocess.run(["git", "check-ignore", "--stdin"], cwd=project,
                                    input=b"backend/.local/.config-interrupted-probe\n", capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), b"backend/.local/.config-interrupted-probe")
        finally:
            for file in staged:
                file.unlink(missing_ok=True)

    def test_old_setup_initializer_also_rejects_replacement_key_for_encrypted_data(self):
        from scripts.initialize_env import create_local_env
        self.seed_encrypted()
        with self.assertRaises(LocalConfigurationError) as error:
            create_local_env(self.folder)
        self.assertEqual(error.exception.code, "ENCRYPTION_KEY_RECOVERY_REQUIRED")
        self.assertFalse((self.folder / ".env").exists())

    def test_setup_initializer_cannot_probe_empty_default_while_process_points_at_ciphertext_database(self):
        from scripts.initialize_env import create_local_env
        self.seed_encrypted()
        old = self.folder / "existing-encrypted.sqlite3"
        (self.folder / "db.sqlite3").rename(old)
        original = old.read_bytes()
        with patch.dict(os.environ, {"SQLITE_PATH": str(old)}):
            with self.assertRaises(LocalConfigurationError) as error:
                create_local_env(self.folder)
        self.assertEqual(error.exception.code, "PROCESS_ENV_AMBIGUOUS")
        self.assertFalse((self.folder / ".env").exists())
        self.assertFalse((self.folder / "db.sqlite3").exists())
        self.assertEqual(old.read_bytes(), original)

    def test_only_explicit_secret_update_replaces_target_field(self):
        self.prepared(fill_missing=True, prompt=lambda name: SECRETS[name])
        before = self.values()
        self.prepared(update_fields=("WECHAT_APP_SECRET",), prompt=lambda _: "new-disposable-secret")
        after = self.values()
        self.assertEqual(after["WECHAT_APP_SECRET"], "new-disposable-secret")
        self.assertEqual({key: value for key, value in after.items() if key != "WECHAT_APP_SECRET"},
                         {key: value for key, value in before.items() if key != "WECHAT_APP_SECRET"})

    def test_invalid_secret_duplicate_keys_and_account_or_sales_changes_leave_files_untouched(self):
        self.prepared()
        baseline = (self.folder / ".env").read_bytes()
        for options in ({"update_fields": ("WECHAT_APP_SECRET",), "prompt": lambda _: "secret\nnew-line"},
                        {"update_fields": ("VIRTUAL_PAYMENT_CALLBACK_AES_KEY",), "prompt": lambda _: "invalid"}):
            with self.assertRaises(LocalConfigurationError):
                self.prepared(**options)
            self.assertEqual((self.folder / ".env").read_bytes(), baseline)
        with self.assertRaisesMessage(LocalConfigurationError, "禁止自动切换"):
            configure(self.folder, "wx0000000000000002", OFFER)
        for suffix in (b"WECHAT_APP_ID=duplicate\n", b"EXTRA=one\nEXTRA=two\n"):
            (self.folder / ".env").write_bytes(baseline + suffix)
            with self.assertRaises(LocalConfigurationError):
                self.prepared()
            self.assertEqual((self.folder / ".env").read_bytes(), baseline + suffix)
        (self.folder / ".env").write_bytes(baseline.replace(b"VIRTUAL_PAYMENT_ENABLED='false'", b"VIRTUAL_PAYMENT_ENABLED='true'"))
        enabled = (self.folder / ".env").read_bytes()
        with self.assertRaises(LocalConfigurationError):
            self.prepared()
        self.assertEqual((self.folder / ".env").read_bytes(), enabled)

    def test_hidden_input_rejects_no_tty_and_no_getpass_echo_fallback(self):
        with patch("scripts.local_configuration.sys.stdin.isatty", return_value=False), patch("scripts.local_configuration.getpass.getpass") as prompt:
            with self.assertRaises(LocalConfigurationError):
                hidden_secret("WECHAT_APP_SECRET")
            prompt.assert_not_called()
        import getpass
        with patch("scripts.local_configuration.sys.stdin.isatty", return_value=True), patch("scripts.local_configuration.sys.stderr.isatty", return_value=True), patch("scripts.local_configuration.getpass.getpass", side_effect=getpass.GetPassWarning("must-not-print")):
            with self.assertRaises(LocalConfigurationError):
                hidden_secret("WECHAT_APP_SECRET")

    def test_old_encrypted_identity_session_or_payment_cache_blocks_key_generation(self):
        for table, column in (("accounts_studentsession", "encrypted_session_key"), ("accounts_wechatidentity", "encrypted_session_key"), ("payments_accesstokencache", "ciphertext")):
            with self.subTest(table=table):
                database = self.folder / "db.sqlite3"
                database.unlink(missing_ok=True)
                self.seed_encrypted(table, column)
                (self.folder / ".env").write_text("DJANGO_SECRET_KEY=existing-private-django-key-for-test\n", encoding="utf-8")
                before = (self.folder / ".env").read_bytes()
                with self.assertRaises(LocalConfigurationError) as error:
                    self.prepared()
                self.assertEqual(error.exception.code, "ENCRYPTION_KEY_RECOVERY_REQUIRED")
                self.assertEqual(before, (self.folder / ".env").read_bytes())

    def test_database_other_app_blocks_identity_adoption(self):
        with sqlite_database(self.folder / "db.sqlite3") as connection:
            connection.execute("CREATE TABLE accounts_wechatidentity (app_id TEXT, encrypted_session_key TEXT)")
            connection.execute("INSERT INTO accounts_wechatidentity VALUES ('wx0000000000000002','')")
        with self.assertRaises(LocalConfigurationError) as error:
            self.prepared()
        self.assertEqual(error.exception.code, "ACCOUNT_DATA_MISMATCH")
        self.assertFalse((self.folder / ".env").exists())

    def test_second_file_failure_rolls_back_profile_and_leaves_environment_unchanged(self):
        self.prepared()
        before_env = (self.folder / ".env").read_bytes()
        before_profile = (self.folder / ".local/wechat-account.json").read_bytes()
        replace = os.replace
        def fail_env(source, destination):
            if Path(destination).name == ".env":
                raise OSError("private input must not be exposed")
            return replace(source, destination)
        with patch("scripts.local_configuration.os.replace", side_effect=fail_env):
            with self.assertRaises(OSError):
                self.prepared(confirmations={"filing": True})
        self.assertEqual(before_env, (self.folder / ".env").read_bytes())
        self.assertEqual(before_profile, (self.folder / ".local/wechat-account.json").read_bytes())
        self.assertFalse(list(self.folder.rglob(".config-*")))

    def test_concurrent_edit_is_preserved_and_no_profile_commit_occurs(self):
        self.prepared()
        profile = (self.folder / ".local/wechat-account.json").read_bytes()
        from scripts.local_configuration import private_temp
        calls = []
        def concurrently_edit(path, data):
            staged = private_temp(path, data)
            calls.append(path)
            if len(calls) == 2:
                with (self.folder / ".env").open("a") as handle:
                    handle.write("CONCURRENT=preserve\n")
            return staged
        with patch("scripts.local_configuration.private_temp", side_effect=concurrently_edit):
            with self.assertRaises(LocalConfigurationError) as error:
                self.prepared(confirmations={"filing": True})
        self.assertEqual(error.exception.code, "CONFIG_CHANGED")
        self.assertIn("CONCURRENT=preserve", (self.folder / ".env").read_text())
        self.assertEqual(profile, (self.folder / ".local/wechat-account.json").read_bytes())

    def test_symlink_target_is_rejected(self):
        target = self.folder / "elsewhere"
        target.write_text("preserve")
        try:
            (self.folder / ".env").symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Host does not permit test symlinks")
        with self.assertRaises(LocalConfigurationError):
            read_private(self.folder / ".env")
        self.assertEqual(target.read_text(), "preserve")

    def test_unknown_secret_argument_does_not_echo_value_or_create_configuration(self):
        script = Path(__file__).resolve().parents[2] / "scripts/configure_local.py"
        result = subprocess.run([sys.executable, str(script), "--app-secret", "PRIVATE_TEST_VALUE"], capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"PRIVATE_TEST_VALUE", result.stdout + result.stderr)

    def test_wrapper_never_echoes_import_exception_or_database_configuration(self):
        from scripts.check_local_readiness import main
        result = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"password=PRIVATE_TEST_VALUE traceback")
        output = io.StringIO()
        with patch.object(sys, "argv", ["check_local_readiness", "--json"]), patch("scripts.check_local_readiness.subprocess.run", return_value=result), redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(main(), 2)
        self.assertNotIn("PRIVATE_TEST_VALUE", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["checks"][0]["code"], "CONFIG_LOAD")

    def test_process_database_offer_or_key_ambiguity_blocks_before_generation(self):
        cases = {"DB_ENGINE": "postgresql", "SQLITE_PATH": "another.sqlite3", "DB_NAME": "another-database",
                 "DB_PASSWORD": "PRIVATE_TEST_VALUE", "VIRTUAL_PAYMENT_OFFER_ID": "9999", "STUDENT_SESSION_ENCRYPTION_KEYS": Fernet.generate_key().decode()}
        for name, value in cases.items():
            with self.subTest(field=name), patch.dict(os.environ, {name: value}):
                with self.assertRaises(LocalConfigurationError) as error:
                    self.prepared()
                self.assertEqual(error.exception.code, "PROCESS_ENV_AMBIGUOUS")
                self.assertFalse((self.folder / ".env").exists())

    def test_rollback_does_not_overwrite_profile_changed_by_another_editor(self):
        self.prepared()
        original_env = (self.folder / ".env").read_bytes()
        replace = os.replace
        profile_path = self.folder / ".local/wechat-account.json"
        def edit_during_failure(source, destination):
            if Path(destination).name == ".env":
                profile_path.write_bytes(b"manual-preserved-profile")
                raise OSError("write failed")
            return replace(source, destination)
        with patch("scripts.local_configuration.os.replace", side_effect=edit_during_failure):
            with self.assertRaises(LocalConfigurationError) as error:
                self.prepared(confirmations={"filing": True})
        self.assertEqual(error.exception.code, "CONFIG_ROLLBACK_CHANGED")
        self.assertEqual(profile_path.read_bytes(), b"manual-preserved-profile")
        self.assertEqual((self.folder / ".env").read_bytes(), original_env)

    def test_environment_edited_after_profile_replace_is_preserved_and_profile_rolled_back(self):
        self.prepared()
        env_path = self.folder / ".env"
        profile_path = self.folder / ".local/wechat-account.json"
        original_env, original_profile = env_path.read_bytes(), profile_path.read_bytes()
        replace = os.replace
        replacements = []
        def change_environment(source, destination):
            result = replace(source, destination)
            replacements.append(destination)
            if len(replacements) == 1:
                env_path.write_bytes(original_env + b"MANUAL_EDIT=preserve\n")
            return result
        with patch("scripts.local_configuration.os.replace", side_effect=change_environment):
            with self.assertRaises(LocalConfigurationError) as error:
                self.prepared(confirmations={"filing": True})
        self.assertEqual(error.exception.code, "CONFIG_CHANGED")
        self.assertEqual(env_path.read_bytes(), original_env + b"MANUAL_EDIT=preserve\n")
        self.assertEqual(profile_path.read_bytes(), original_profile)

    def test_ring_validation_does_not_depend_on_assert_optimization(self):
        from scripts.local_configuration import key_ring_valid
        with patch("scripts.local_configuration.Fernet") as cipher:
            cipher.return_value.decrypt.return_value = b"wrong-plaintext"
            self.assertFalse(key_ring_valid(Fernet.generate_key().decode()))

    def test_active_transaction_blocks_secret_update_without_altering_files(self):
        self.prepared(fill_missing=True, prompt=lambda name: SECRETS[name])
        original = (self.folder / ".env").read_bytes()
        with sqlite_database(self.folder / "db.sqlite3") as db:
            db.execute("CREATE TABLE payments_order (app_id TEXT,status TEXT)")
            db.execute("INSERT INTO payments_order VALUES (?, 'preparing')", (APP,))
        with self.assertRaises(LocalConfigurationError) as error:
            self.prepared(update_fields=("VIRTUAL_PAYMENT_APP_KEY",), prompt=lambda _: "updated-disposable-key")
        self.assertEqual(error.exception.code, "ACTIVE_TRANSACTION_BLOCKED")
        self.assertEqual((self.folder / ".env").read_bytes(), original)

    def test_profile_duplicate_identifiers_are_rejected_without_rewriting_environment(self):
        self.prepared()
        original = (self.folder / ".env").read_bytes()
        profile = self.folder / ".local/wechat-account.json"
        valid = profile.read_text(encoding="utf-8")
        app_field = '"appId": "' + APP + '"'
        profile.write_text(valid.replace(app_field, app_field + "," + app_field, 1), encoding="utf-8")
        with self.assertRaises(LocalConfigurationError) as error:
            self.prepared()
        self.assertEqual(error.exception.code, "PROFILE_INVALID")
        self.assertEqual((self.folder / ".env").read_bytes(), original)
