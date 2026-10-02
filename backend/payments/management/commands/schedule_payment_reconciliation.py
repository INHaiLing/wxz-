from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from payments.models import PaymentTask
from payments.tasks import schedule_reconciliation


class Command(BaseCommand):
    help = "重新安排已完成订单查单，以捕获遗漏的退款通知；失败任务需人工确认。"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--older-than-hours", type=int, default=24)

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 1000 or not 1 <= options["older_than_hours"] <= 8760:
            raise CommandError("limit 1～1000，older-than-hours 1～8760。")
        cutoff = timezone.now() - timedelta(hours=options["older_than_hours"])
        orders = list(PaymentTask.objects.filter(kind="query", status="done", updated_at__lte=cutoff,
            order__status__in=("preparing", "paid", "fulfilled", "review")).order_by("updated_at", "pk").values_list("order_id", flat=True)[:options["limit"]])
        count = sum(schedule_reconciliation(pk, cutoff=cutoff) for pk in orders)
        self.stdout.write(f"已重新安排 {count} 个订单查单。")
