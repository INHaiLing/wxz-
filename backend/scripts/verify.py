"""Run the complete backend checks against a disposable environment."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]


def run(*arguments, env):
    subprocess.run([sys.executable, *arguments], cwd=ROOT, env=env, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", choices=("sqlite", "postgresql"), default="sqlite")
    args = parser.parse_args()
    env = dict(os.environ, PYTHONUTF8="1", DJANGO_SETTINGS_MODULE="config.test_settings", TEST_DB_ENGINE=args.database)
    with TemporaryDirectory(prefix="chinese-study-check-") as directory:
        env["TEST_SQLITE_PATH"] = str(Path(directory) / "bootstrap.sqlite3")
        run("-m", "pip", "check", env=env)
        run(str(ROOT / "scripts" / "check_lock.py"), env=env)
        run("-c", "import psycopg; from cryptography.fernet import Fernet; key=Fernet.generate_key(); f=Fernet(key); assert f.decrypt(f.encrypt(b'compatibility')) == b'compatibility'; print('PASS: PostgreSQL driver and encryption wheel imports.')", env=env)
        for command in (
            ("check",),
            ("makemigrations", "--check", "--dry-run"),
            ("migrate", "--noinput"),
            ("seed_demo",),
            ("seed_demo",),
            ("setup_roles",),
            ("setup_roles",),
            ("seed_product",),
            ("seed_product",),
            ("test", "--noinput", "--verbosity", "2"),
        ):
            run("manage.py", *command, env=env)
    print(f"PASS: isolated {args.database} backend verification.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
