from django.core.management.base import BaseCommand

from entitlements.models import SCOPE, Product


class Command(BaseCommand):
    help = "初始化全语文题库商品（不覆盖既有价格及配置）。"

    def handle(self, *args, **options):
        product, created = Product.objects.get_or_create(scope=SCOPE, defaults={"id": "all-chinese", "price_fen": 1000})
        self.stdout.write(f"{'已创建' if created else '保留现有'}商品 {product.pk}，金额 {product.price_fen} 分。")
