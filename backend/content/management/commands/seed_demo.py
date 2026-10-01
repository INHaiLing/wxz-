"""Initialize only missing demo rows. Never update a user's edited content."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from content.models import Article, Category, Question


class Command(BaseCommand):
    help = "导入现有小程序演示题为草稿；重复运行不覆盖已有数据。"

    @transaction.atomic
    def handle(self, *args, **options):
        fixture = Path(__file__).resolve().parents[2] / "fixtures" / "demo.json"
        data = json.loads(fixture.read_text(encoding="utf-8"))
        totals = {"categories": 0, "articles": 0, "questions": 0}
        for index, row in enumerate(data["categories"], 1):
            _, created = Category.objects.get_or_create(
                pk=row["id"], defaults={"name": row["name"], "sort_order": index}
            )
            totals["categories"] += int(created)
        for row in data["articles"]:
            if row.get("placeholder"):
                continue
            _, created = Article.objects.get_or_create(
                pk=row["id"], defaults={"title": row["title"], "sort_order": row["index"]}
            )
            totals["articles"] += int(created)
        for index, row in enumerate(data["questions"], 1):
            if Question.objects.filter(pk=row["id"]).exists():
                continue
            question = Question(
                id=row["id"], source=row["source"],
                category_id=row.get("categoryId"), article_id=row.get("articleId"),
                type=row["type"], tag=row["tag"], stem=row["stem"],
                answers=row["answers"], sort_order=index,
            )
            question.full_clean()
            question.save()
            totals["questions"] += 1
        self.stdout.write(self.style.SUCCESS(
            "演示数据初始化完成：新增分类 {categories}、篇目 {articles}、草稿题目 {questions}。"
            "已有记录未修改；演示内容不代表正式题库。".format(**totals)
        ))
