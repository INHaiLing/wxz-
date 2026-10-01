from io import StringIO

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import Client, TestCase

from accounts.models import User
from content.models import Article, Category, Question


class BootstrapTests(TestCase):
    def test_seed_is_draft_only_and_preserves_edited_records(self):
        output = StringIO()
        call_command("seed_demo", stdout=output)
        self.assertEqual(Category.objects.count(), 8)
        self.assertEqual(Article.objects.count(), 23)
        self.assertEqual(Question.objects.count(), 16)
        self.assertFalse(Question.objects.filter(is_published=True).exists())
        question = Question.objects.get(pk="word-ri")
        question.tag = "人工修订标签"
        question.save()
        call_command("seed_demo", stdout=output)
        question.refresh_from_db()
        self.assertEqual(question.tag, "人工修订标签")
        self.assertEqual(Question.objects.count(), 16)

    def test_roles_separate_edit_publish_and_do_not_reset_customization(self):
        call_command("setup_roles", stdout=StringIO())
        editor = Group.objects.get(name="题库编辑")
        publisher = Group.objects.get(name="题库发布")
        self.assertTrue(editor.permissions.filter(codename="import_question").exists())
        self.assertFalse(editor.permissions.filter(codename="publish_question").exists())
        self.assertTrue(publisher.permissions.filter(codename="publish_question").exists())
        self.assertFalse(publisher.permissions.filter(codename="change_question").exists())
        self.assertFalse(editor.permissions.filter(content_type__app_label="accounts").exists())
        removed = Permission.objects.get(content_type__app_label="content", codename="add_question")
        editor.permissions.remove(removed)
        call_command("setup_roles", stdout=StringIO())
        self.assertFalse(editor.permissions.filter(pk=removed.pk).exists())

    def test_admin_login_requires_csrf_and_valid_password(self):
        user = User.objects.create_superuser(username="operator", password="test-only-valid-passphrase-728")
        browser = Client(enforce_csrf_checks=True)
        response = browser.get("/admin/login/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(browser.post("/admin/login/", {
            "username": user.username, "password": "test-only-valid-passphrase-728",
        }).status_code, 403)
        response = browser.post("/admin/login/", {
            "username": user.username, "password": "test-only-valid-passphrase-728",
            "csrfmiddlewaretoken": browser.cookies["csrftoken"].value, "next": "/admin/",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(browser.get("/admin/").status_code, 200)
