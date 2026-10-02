"""Re-encrypt identity and per-login keys using the configured primary key."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.crypto import rotate_session_key, session_cipher
from accounts.models import StudentSession, User, WeChatIdentity
from common.errors import BusinessError


class Command(BaseCommand):
    help = "按批重新加密身份及学员会话密钥；保留旧密钥直到迁移、备份验证完成。"

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        size = options["batch_size"]
        if not 1 <= size <= 1000:
            raise CommandError("batch-size 必须为 1～1000。")
        counts = []
        try:
            session_cipher()
            for model in (WeChatIdentity, StudentSession):
                count, last_id = 0, 0
                while True:
                    rows = list(model.objects.filter(pk__gt=last_id).order_by("pk").values_list("pk", "user_id")[:size])
                    if not rows:
                        break
                    with transaction.atomic():
                        list(User.objects.select_for_update().filter(pk__in={owner for _, owner in rows}).order_by("pk"))
                        objects = list(model.objects.select_for_update().filter(pk__in=[pk for pk, _ in rows]).order_by("pk"))
                        for obj in objects:
                            obj.encrypted_session_key = rotate_session_key(obj.encrypted_session_key)
                        if not options["dry_run"]:
                            model.objects.bulk_update(objects, ("encrypted_session_key",))
                    count += len(objects)
                    last_id = rows[-1][0]
                counts.append(count)
        except BusinessError:
            raise CommandError("密钥轮换失败；当前批已回滚。请检查主密钥及全部历史密钥，修正后重新运行。") from None
        prefix = "预检" if options["dry_run"] else "轮换"
        self.stdout.write(f"{prefix}完成：身份 {counts[0]}，学员会话 {counts[1]}；未输出明文。")
