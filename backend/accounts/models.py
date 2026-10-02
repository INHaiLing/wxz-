from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db import models


class User(AbstractUser):
    """Stable internal account; external WeChat identities can be linked later."""

    class Meta(AbstractUser.Meta):
        verbose_name = "后台账号"
        verbose_name_plural = "后台账号"


class WeChatIdentity(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wechat_identities")
    app_id = models.CharField("小程序 AppID", max_length=64)
    openid = models.CharField("微信身份", max_length=128)
    encrypted_session_key = models.TextField("加密微信会话密钥")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新登录时间", auto_now=True)

    class Meta:
        verbose_name = "学员微信身份"
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=("app_id", "openid"), name="unique_wechat_identity")]

    def __str__(self):
        return f"微信身份 #{self.pk} / 用户 #{self.user_id}"


class StudentSession(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="student_sessions")
    identity = models.ForeignKey(WeChatIdentity, on_delete=models.PROTECT, null=True, blank=True, related_name="sessions")
    token_digest = models.CharField("会话摘要", max_length=64, unique=True)
    encrypted_session_key = models.TextField("本次登录加密微信会话密钥")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    expires_at = models.DateTimeField("失效时间", db_index=True)
    revoked_at = models.DateTimeField("撤销时间", null=True, blank=True)

    class Meta:
        verbose_name = "学员会话"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"学员会话 #{self.pk} / 用户 #{self.user_id}"
