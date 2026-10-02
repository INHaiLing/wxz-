"""Bounded safe wrapper: configuration import errors never reveal .env values."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("unsupported arguments")


def main():
    parser = SafeParser(description="只读离线检查本机联调准备，不联网，不输出秘密。")
    parser.add_argument("--json", action="store_true")
    try:
        options = parser.parse_args()
    except ValueError:
        print("CLI_ARGUMENTS_INVALID: 本检查只接受 --json，不输出原始参数。", file=sys.stderr)
        return 2
    command = [sys.executable, "manage.py", "check_local_readiness", "--json"]
    report = {"checks": [{"code": "CONFIG_LOAD", "status": "blocked", "message": "本机配置无法安全加载或诊断超时；请先检查配置，不输出异常详情。"}]}
    try:
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                                env=dict(os.environ, PYTHONUTF8="1"), capture_output=True, timeout=10)
        parsed = json.loads(result.stdout)
        if set(parsed) == {"checks"} and isinstance(parsed["checks"], list) and all(
            set(check) == {"code", "status", "message"} and check["status"] in ("ready", "missing", "blocked", "pending")
            for check in parsed["checks"]
        ):
            report = parsed
    except (Exception, KeyboardInterrupt):
        pass
    if options.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        for check in report["checks"]:
            print(f"{check['code']} [{check['status']}]: {check['message']}")
    return 2 if any(check["status"] in ("missing", "blocked") for check in report["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main())
