"""Create configuration once; preserve all existing credentials on repeat setup."""
import base64
import secrets
from pathlib import Path


def create_local_env(directory):
    directory = Path(directory)
    target = directory / ".env"
    text = (directory / ".env.example").read_text(encoding="utf-8")
    text = text.replace("replace-with-generated-secret-at-least-32-characters", secrets.token_urlsafe(64))
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    text = text.replace("STUDENT_SESSION_ENCRYPTION_KEYS=\n", f"STUDENT_SESSION_ENCRYPTION_KEYS={key}\n")
    try:
        with target.open("x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError:
        return False
    return True


if __name__ == "__main__":
    created = create_local_env(Path(__file__).resolve().parents[1])
    print("Local configuration created." if created else "Existing configuration retained.")
