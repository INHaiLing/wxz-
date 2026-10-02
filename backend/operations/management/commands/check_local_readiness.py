import json

from django.core.management.base import BaseCommand, CommandError

from operations.local_readiness import collect_readiness


class Command(BaseCommand):
    help = "只读离线检查本机配置、角色及商品；固定检查码，不联网、不输出秘密。"

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        report = collect_readiness()
        if options["json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False))
        else:
            for check in report["checks"]:
                self.stdout.write(f"{check['code']} [{check['status']}]: {check['message']}")
        if any(check["status"] in ("missing", "blocked") for check in report["checks"]):
            raise CommandError("LOCAL_READINESS_INCOMPLETE: 本机配置仍有缺口；报告未包含秘密。")
