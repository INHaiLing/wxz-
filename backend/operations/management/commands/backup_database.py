from django.core.management.base import BaseCommand
from operations.backup import backup_database


class Command(BaseCommand):
    help = "创建权限受限、不可覆盖的数据库快照。"

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        backup_database(options["output"])
        self.stdout.write("数据库备份已完成；请在独立库验证恢复并转存受控备份存储。")
