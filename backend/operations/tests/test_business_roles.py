import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from operations.management.commands.setup_business_roles import ROLES


class BusinessRoleTests(TestCase):
    def initialize(self):
        call_command("setup_business_roles", stdout=io.StringIO())

    def test_fresh_roles_have_exact_grants_and_do_not_create_or_assign_users(self):
        user = get_user_model().objects.create_user(username="roles-existing-operator", password=None, is_staff=True)
        before = get_user_model().objects.count()
        self.initialize()
        self.assertEqual(get_user_model().objects.count(), before)
        self.assertFalse(user.groups.exists())
        for name, expected in ROLES.items():
            with self.subTest(role=name):
                actual = set(Group.objects.get(name=name).permissions.values_list(
                    "content_type__app_label", "content_type__model", "codename"))
                self.assertEqual(actual, expected)

    def test_existing_customized_role_is_preserved_even_when_empty(self):
        group = Group.objects.create(name="商品运营")
        custom = Permission.objects.get(content_type__app_label="content", codename="view_article")
        group.permissions.add(custom)
        empty = Group.objects.create(name="学员启停")
        self.initialize()
        self.initialize()
        self.assertEqual(list(group.permissions.all()), [custom])
        self.assertFalse(empty.permissions.exists())
        self.assertEqual(Group.objects.filter(name__in=ROLES).count(), 6)

    def test_missing_permission_fails_before_creating_any_group(self):
        broken = {**ROLES, "新业务": frozenset({("accounts", "studentaccount", "permission_does_not_exist")})}
        with patch("operations.management.commands.setup_business_roles.ROLES", broken):
            with self.assertRaisesMessage(CommandError, "permission_does_not_exist"):
                self.initialize()
        self.assertFalse(Group.objects.filter(name__in=ROLES).exists())

    def test_permission_codename_for_wrong_model_is_not_accepted(self):
        wrong = {"错误模型": frozenset({("content", "article", "view_question")})}
        with patch("operations.management.commands.setup_business_roles.ROLES", wrong):
            with self.assertRaisesMessage(CommandError, "content.article.view_question"):
                self.initialize()
        self.assertFalse(Group.objects.filter(name="错误模型").exists())

    def test_stale_permission_row_for_uninstalled_model_is_rejected(self):
        type_ = ContentType.objects.create(app_label="retired_module", model="legacy_model")
        Permission.objects.create(content_type=type_, codename="view_legacy_model", name="历史权限")
        stale = {"历史模块": frozenset({("retired_module", "legacy_model", "view_legacy_model")})}
        with patch("operations.management.commands.setup_business_roles.ROLES", stale):
            with self.assertRaisesMessage(CommandError, "retired_module.legacy_model.view_legacy_model"):
                self.initialize()
        self.assertFalse(Group.objects.filter(name="历史模块").exists())

    def test_effective_roles_cannot_gain_unrelated_high_impact_permissions(self):
        self.initialize()
        matrix = {
            "业务查看": ("content.view_question", ("content.change_question", "entitlements.sync_product")),
            "学员启停": ("accounts.set_student_status", ("accounts.change_user", "auth.change_group")),
            "商品运营": ("entitlements.sync_product", ("activation.generate_activationbatch", "entitlements.revoke_entitlementsource")),
            "激活码管理": ("activation.generate_activationbatch", ("entitlements.revoke_entitlementsource", "entitlements.change_product")),
            "权益撤销": ("entitlements.revoke_entitlementsource", ("activation.generate_activationbatch", "entitlements.change_product")),
            "支付核查": ("payments.reschedule_paymenttask", ("payments.change_order", "entitlements.grant_entitlementsource")),
        }
        for index, (name, (allowed, forbidden)) in enumerate(matrix.items()):
            with self.subTest(role=name):
                user = get_user_model().objects.create_user(username=f"roles-{index}", password=None, is_staff=True)
                user.groups.add(Group.objects.get(name=name))
                self.assertTrue(user.has_perm(allowed))
                self.assertTrue(all(not user.has_perm(permission) for permission in forbidden))
