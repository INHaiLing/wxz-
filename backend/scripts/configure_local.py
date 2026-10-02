"""Local hidden-input configurator. Secret values are never CLI arguments."""
import argparse
from pathlib import Path
import sys

from local_configuration import (CONFIRMATIONS, LocalConfigurationError, OPTIONAL_SECRET_FIELDS,
                                 SECRET_FIELDS, configure)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise LocalConfigurationError("CLI_ARGUMENTS_INVALID", "命令参数无效；秘密值只能使用隐藏交互输入。")


def main():
    parser = SafeParser(description="配置本机微信账号；秘密仅终端隐藏输入，不启用销售。")
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--offer-id", required=True)
    for option in ("filing", "virtual-payment", "apple", "member"):
        parser.add_argument("--confirmed-" + option, action="store_true")
    parser.add_argument("--fill-missing-secrets", action="store_true")
    parser.add_argument("--update-secret", choices=SECRET_FIELDS + OPTIONAL_SECRET_FIELDS, action="append", default=[])
    try:
        options = parser.parse_args()
    except LocalConfigurationError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        return 2
    confirmations = dict(zip(CONFIRMATIONS, (options.confirmed_filing, options.confirmed_virtual_payment,
                                            options.confirmed_apple, options.confirmed_member)))
    try:
        missing = configure(Path(__file__).resolve().parents[1], options.app_id, options.offer_id, confirmations,
                            fill_missing=options.fill_missing_secrets, update_fields=options.update_secret)
    except LocalConfigurationError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        return 2
    except (Exception, KeyboardInterrupt):
        print("CONFIG_WRITE_FAILED: 配置未完整提交；请在本机核对，未输出秘密。", file=sys.stderr)
        return 2
    print("本机账号档案与配置已保存；销售三开关关闭，平台确认不代表真实联调验收。")
    if missing:
        print("SECRET_MISSING: 缺少 " + "、".join(missing) + "；请在交互终端隐藏录入。")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
