from django.core.management.base import BaseCommand, CommandError

from contracts.validation import load_document, validate_document


class Command(BaseCommand):
    help = "只读校验OpenAPI、真实学员路由、认证、输入字段及示例。"

    def handle(self, *args, **options):
        try:
            report = validate_document(load_document())
        except (OSError, ValueError) as error:
            raise CommandError("无法读取接口契约，请先生成并检查JSON。") from error
        if report["errors"]:
            raise CommandError("\n".join(report["errors"]))
        self.stdout.write(self.style.SUCCESS(f"学员契约通过：{report['operationCount']} 个HTTP操作，路由完全覆盖。"))
