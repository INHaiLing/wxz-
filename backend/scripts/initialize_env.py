"""Create configuration once; preserve all existing credentials on repeat setup."""
import base64
import secrets
import os
from pathlib import Path
import sys

try:
    from .local_configuration import LocalConfigurationError, parse_env, private_temp, read_private, reject_process_ambiguity, safe_probe
except ImportError:  # Direct invocation from setup.ps1.
    from local_configuration import LocalConfigurationError, parse_env, private_temp, read_private, reject_process_ambiguity, safe_probe


def create_local_env(directory):
    directory = Path(directory)
    target = directory / ".env"
    if read_private(target) is not None:
        return False
    text = (directory / ".env.example").read_text(encoding="utf-8")
    _, _, values = parse_env(text)
    reject_process_ambiguity(values)
    if safe_probe(directory, values)["encrypted"]:
        raise LocalConfigurationError("ENCRYPTION_KEY_RECOVERY_REQUIRED", "存在旧加密数据，请恢复原配置及会话密钥；禁止新建替代密钥。")
    text = text.replace("replace-with-generated-secret-at-least-32-characters", secrets.token_urlsafe(64))
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    text = text.replace("STUDENT_SESSION_ENCRYPTION_KEYS=\n", f"STUDENT_SESSION_ENCRYPTION_KEYS={key}\n")
    staged = private_temp(target, text.encode("utf-8"))
    try:
        # Atomic exclusive publication; an existing .env is never overwritten.
        try:
            os.link(staged, target)
        except FileExistsError:
            return False
    finally:
        staged.unlink(missing_ok=True)
    return True


if __name__ == "__main__":
    try:
        created = create_local_env(Path(__file__).resolve().parents[1])
        print("Local configuration created." if created else "Existing configuration retained.")
    except LocalConfigurationError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        sys.exit(2)
    except Exception:
        print("CONFIG_INITIALIZATION_FAILED: 无法安全初始化本机配置，未输出秘密。", file=sys.stderr)
        sys.exit(2)
