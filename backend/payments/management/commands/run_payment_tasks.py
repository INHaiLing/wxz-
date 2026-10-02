from django.core.management.base import BaseCommand, CommandError
from payments.tasks import claim_task, run_task


class Command(BaseCommand):
    help = "执行有限数量的到期支付补偿任务，可由多个 worker 并行调用。"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 1000:
            raise CommandError("limit 必须为 1～1000。")
        count = 0
        for _ in range(limit):
            claimed = claim_task()
            if claimed is None:
                break
            run_task(*claimed)
            count += 1
        self.stdout.write(f"已处理 {count} 个支付任务；结果和安全错误码保存在后台。")
