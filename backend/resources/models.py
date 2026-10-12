"""Single active configuration for the student resource group entry point."""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q

from .storage import PublicGroupQRStorage


MAX_QR_IMAGE_SIZE = 2 * 1024 * 1024
QR_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def validate_qr_image(upload):
    name = upload.name.lower()
    extension = name[name.rfind("."):] if "." in name else ""
    expected_type = QR_IMAGE_CONTENT_TYPES.get(extension)
    if expected_type is None or (
        hasattr(upload, "content_type") and upload.content_type != expected_type
    ):
        raise ValidationError("二维码仅支持 PNG、JPEG 或 WebP 图片，文件扩展名和类型必须匹配。")
    if upload.size > MAX_QR_IMAGE_SIZE:
        raise ValidationError("二维码图片不能超过 2MB。")

    position = upload.tell() if hasattr(upload, "tell") else 0
    header = upload.read(12)
    upload.seek(position)
    valid_signature = (
        (expected_type == "image/png" and header.startswith(b"\x89PNG\r\n\x1a\n"))
        or (expected_type == "image/jpeg" and header.startswith(b"\xff\xd8\xff"))
        or (expected_type == "image/webp" and header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )
    if not valid_signature:
        raise ValidationError("图片内容与声明的格式不一致。")


def group_qr_upload_to(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower()
    return f"resources/group/{uuid.uuid4().hex}.{extension}"


class ConfigurationQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("资料群配置不能删除，请停用配置。")


class GroupResourceConfiguration(models.Model):
    objects = ConfigurationQuerySet.as_manager()
    group_wechat_id = models.CharField("微信号", max_length=128, blank=True)
    group_qr_code = models.FileField(
        "微信群二维码", upload_to=group_qr_upload_to, blank=True,
        storage=PublicGroupQRStorage(),
        validators=(FileExtensionValidator(allowed_extensions=tuple(ext[1:] for ext in QR_IMAGE_CONTENT_TYPES)),
                    validate_qr_image),
    )
    claim_code = models.CharField("领取口令", max_length=160, blank=True)
    description = models.TextField("资料说明", blank=True)
    announcement = models.TextField("群公告", blank=True)
    is_enabled = models.BooleanField("启用", default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="最后修改人", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="updated_group_resource_configurations",
    )
    updated_at = models.DateTimeField("修改时间", auto_now=True)

    class Meta:
        verbose_name = "资料群配置"
        verbose_name_plural = verbose_name
        default_permissions = ("view", "add", "change", "delete")
        constraints = [models.CheckConstraint(condition=Q(pk=1), name="resources_group_config_singleton")]

    def __str__(self):
        return "资料群配置"

    @classmethod
    def current(cls):
        return cls.objects.order_by("pk").first()

    def clean(self):
        super().clean()
        if self.pk not in (None, 1):
            raise ValidationError("资料群配置只能保留一条记录。")

    def save(self, *args, **kwargs):
        if self._state.adding and type(self).objects.filter(pk=1).exists():
            raise ValidationError("资料群配置只能保留一条记录。")
        if self.pk not in (None, 1):
            raise ValidationError("资料群配置只能使用固定编号 1。")
        self.pk = 1
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("资料群配置不能删除，请停用配置。")
