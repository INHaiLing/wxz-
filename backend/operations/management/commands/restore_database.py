from django.core.management.base import BaseCommand
from operations.backup import restore_database


class Command(BaseCommand):
    help = "离线恢复到独立新 SQLite 文件或预先创建的 PostgreSQL 空库。"

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True)
        parser.add_argument("--target", required=True)

    def handle(self, *args, **options):
        restore_database(options["input"], options["target"])
        self.stdout.write("独立目标恢复完成；请核对迁移、业务数据与加密密钥后再切换服务配置。")
