"""Create missing operational roles without expanding existing grants."""

from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


def permissions(app, model, *codes):
    return frozenset((app, model, code) for code in codes)


VIEW_PERMISSIONS = frozenset().union(
    *(permissions("content", model, "view_" + model) for model in
      ("category", "article", "question", "questionrevision", "learningconfiguration")),
    *(permissions("accounts", model, "view_" + model) for model in
      ("studentaccount", "wechatidentity", "studentsession")),
    *(permissions("entitlements", model, "view_" + model) for model in
      ("product", "entitlementsource", "openingreservation", "auditevent")),
    *(permissions("activation", model, "view_" + model) for model in
      ("activationbatch", "activationcode", "activationredemption")),
    *(permissions("learning", model, "view_" + model) for model in
      ("questionstate", "learningactivity", "learningpreference")),
    *(permissions("practice", model, "view_" + model) for model in
      ("practiceround", "rounditem", "resumeposition")),
    *(permissions("payments", model, "view_" + model) for model in
      ("order", "paymenttask", "paymentevent")),
)

ROLES = {
    "业务查看": VIEW_PERMISSIONS,
    "学员启停": permissions("accounts", "studentaccount", "view_studentaccount", "set_student_status"),
    "商品运营": permissions("entitlements", "product", "view_product", "change_product", "sync_product"),
    "激活码管理": permissions("activation", "activationbatch", "view_activationbatch", "generate_activationbatch",
                          "receive_activationbatch", "enable_activationbatch", "disable_activationbatch", "void_activationbatch")
                    | permissions("activation", "activationcode", "view_activationcode", "disable_activationcode")
                    | permissions("activation", "activationredemption", "view_activationredemption"),
    "权益撤销": permissions("entitlements", "entitlementsource", "view_entitlementsource", "revoke_entitlementsource")
                    | permissions("entitlements", "auditevent", "view_auditevent"),
    "支付核查": permissions("payments", "order", "view_order")
                    | permissions("payments", "paymenttask", "view_paymenttask", "reschedule_paymenttask")
                    | permissions("payments", "paymentevent", "view_paymentevent")
                    | permissions("entitlements", "openingreservation", "view_openingreservation"),
}


class Command(BaseCommand):
    help = "创建缺少的六个业务权限组；保留已有权限，不创建或自动分组任何账号。"

    @transaction.atomic
    def handle(self, *args, **options):
        expected = frozenset().union(*ROLES.values())
        declared = set()
        for app, model_name, code in expected:
            try:
                model = apps.get_model(app, model_name)
            except LookupError:
                continue
            codes = {f"{action}_{model._meta.model_name}" for action in model._meta.default_permissions}
            codes.update(name for name, _ in model._meta.permissions)
            if code in codes:
                declared.add((app, model_name, code))
        actual = {
            (permission.content_type.app_label, permission.content_type.model, permission.codename): permission
            for permission in Permission.objects.select_related("content_type").filter(
                content_type__app_label__in={item[0] for item in expected},
                codename__in={item[2] for item in expected},
            )
        }
        missing = (expected - declared) | (expected - actual.keys())
        if missing:
            names = ", ".join(".".join(item) for item in sorted(missing))
            raise CommandError(f"缺少实际业务权限，请先同步模块并 migrate：{names}")
        for name, required in ROLES.items():
            group, created = Group.objects.get_or_create(name=name)
            if created:
                group.permissions.set([actual[item] for item in sorted(required)])
            self.stdout.write(f"{name}：{'已创建' if created else '已存在，保留当前权限'}")
