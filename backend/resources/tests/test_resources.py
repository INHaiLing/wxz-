import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from rest_framework.test import APIClient

from contracts.validation import assert_response
from resources.models import GroupResourceConfiguration, MAX_QR_IMAGE_SIZE, validate_qr_image


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


@override_settings(ROOT_URLCONF="resources.tests.urls")
class GroupResourceAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_anonymous_reads_only_enabled_public_fields(self):
        response = self.client.get("/api/student/v1/resources/group/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"enabled": False})
        assert_response(response, "/api/student/v1/resources/group/", "GET")

        config = GroupResourceConfiguration.objects.create(
            is_enabled=True, group_wechat_id="wx-study", claim_code="STUDY-2026",
            description="领取语文资料", announcement="每周更新。",
        )
        response = self.client.get("/api/student/v1/resources/group/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["wechatId"], "wx-study")
        self.assertEqual(response.data["claimCode"], "STUDY-2026")
        self.assertIsNone(response.data["groupQrCode"])
        self.assertNotIn("updatedBy", response.data)
        self.assertNotIn("MEDIA_ROOT", str(response.data))
        assert_response(response, "/api/student/v1/resources/group/", "GET")
        self.assertEqual(self.client.get("/api/student/v1/resources/group/?unexpected=1").status_code, 400)

        config.is_enabled = False
        config.save()
        response = self.client.get("/api/student/v1/resources/group/")
        self.assertEqual(response.data, {"enabled": False})

    def test_explicit_invalid_bearer_is_rejected_on_public_endpoint(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
        self.assertEqual(self.client.get("/api/student/v1/resources/group/").status_code, 401)


@override_settings(ROOT_URLCONF="resources.tests.urls")
class GroupResourceAdminAndValidationTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory(prefix="resources-tests-")
        self.settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.settings_override.enable()
        self.viewer = get_user_model().objects.create_user("resource-viewer", is_staff=True)
        self.editor = get_user_model().objects.create_user("resource-editor", is_staff=True)
        self.editor.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="resources",
            codename="change_groupresourceconfiguration",
        ))
        self.client = Client()

    def tearDown(self):
        self.settings_override.disable()
        self.media_directory.cleanup()

    def test_upload_requires_supported_extension_mime_signature_and_size(self):
        for name, content_type, content in (
            ("bad.png", "image/png", b"not a PNG"),
            ("mismatch.jpg", "image/png", PNG),
            ("unsupported.gif", "image/gif", b"GIF89a" + b"\x00" * 8),
        ):
            with self.subTest(name=name):
                upload = SimpleUploadedFile(name, content, content_type=content_type)
                with self.assertRaises(ValidationError):
                    validate_qr_image(upload)
        too_large = SimpleUploadedFile(
            "large.png", PNG + b"x" * MAX_QR_IMAGE_SIZE, content_type="image/png",
        )
        with self.assertRaises(ValidationError):
            validate_qr_image(too_large)

    def test_admin_permissions_allow_only_authorized_edit_and_keep_singleton(self):
        self.client.force_login(self.viewer)
        self.viewer.user_permissions.add(Permission.objects.get(
            content_type__app_label="resources", codename="view_groupresourceconfiguration",
        ))
        self.assertEqual(self.client.get("/admin/resources/groupresourceconfiguration/").status_code, 200)
        self.assertEqual(self.client.get("/admin/resources/groupresourceconfiguration/add/").status_code, 403)
        self.client.force_login(self.editor)
        self.assertEqual(self.client.get("/admin/resources/groupresourceconfiguration/").status_code, 200)
        item = GroupResourceConfiguration.objects.create()
        self.assertEqual(item.pk, 1)
        self.assertNotEqual(self.client.get("/admin/resources/groupresourceconfiguration/add/").status_code, 200)
        self.client.force_login(self.viewer)
        change_url = "/admin/resources/groupresourceconfiguration/1/change/"
        self.assertEqual(self.client.get(change_url).status_code, 200)
        self.assertEqual(self.client.post(change_url, {"group_wechat_id": "forged"}).status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.group_wechat_id, "")
        self.client.force_login(self.editor)
        with self.assertRaises(ValidationError):
            GroupResourceConfiguration().save()
        with self.assertRaises(ValidationError):
            GroupResourceConfiguration(pk=2).save()
        with self.assertRaises(ValidationError):
            item.delete()
        with self.assertRaises(ValidationError):
            GroupResourceConfiguration.objects.filter(pk=1).delete()

    def test_admin_upload_is_persisted_and_public_endpoint_returns_latest_url(self):
        self.client.force_login(self.editor)
        upload = SimpleUploadedFile("group.png", PNG, content_type="image/png")
        response = self.client.post("/admin/resources/groupresourceconfiguration/add/", {
            "is_enabled": "on", "group_wechat_id": "wx-latest", "claim_code": "LATEST",
            "description": "新资料", "announcement": "最新公告", "group_qr_code": upload,
        })
        self.assertEqual(response.status_code, 302)
        config = GroupResourceConfiguration.objects.get(pk=1)
        self.assertTrue(Path(config.group_qr_code.path).is_file())
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(Path(config.group_qr_code.path).stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(Path(config.group_qr_code.path).parent.stat().st_mode), 0o755)
        public = self.client.get("/api/student/v1/resources/group/")
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()["wechatId"], "wx-latest")
        self.assertIn("/media/resources/group/", public.json()["groupQrCode"])
