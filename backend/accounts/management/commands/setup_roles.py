from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "创建缺少的题库查看/编辑/发布权限组；不改动已有组的权限。"

    @transaction.atomic
    def handle(self, *args, **options):
        view = {"view_category", "view_article", "view_question", "view_questionrevision"}
        roles = {
            "题库查看": view,
            "题库编辑": view | {
                "add_category", "change_category", "add_article", "change_article",
                "add_question", "change_question", "import_question",
            },
            "题库发布": view | {"publish_question"},
        }
        for name, codes in roles.items():
            group, created = Group.objects.get_or_create(name=name)
            if created:
                permissions = Permission.objects.filter(
                    content_type__app_label="content", codename__in=codes
                )
                missing = codes - set(permissions.values_list("codename", flat=True))
                if missing:
                    raise RuntimeError(f"Missing permissions; run migrate first: {sorted(missing)}")
                group.permissions.set(permissions)
            self.stdout.write(f"{name}：{'已创建' if created else '已存在，保留当前权限'}")
