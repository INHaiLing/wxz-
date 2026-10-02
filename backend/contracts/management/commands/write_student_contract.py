from django.core.management.base import BaseCommand

from contracts.document import write_document


class Command(BaseCommand):
    help = "按已审核响应定义及真实serializer/路由更新版本化OpenAPI文档。"

    def handle(self, *args, **options):
        document = write_document()
        self.stdout.write(f"已写学员OpenAPI：{len(document['paths'])} 条实际路径；请执行check_student_contract并审查diff。")
