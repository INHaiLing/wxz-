from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Stable internal account; external WeChat identities can be linked later."""

    class Meta(AbstractUser.Meta):
        verbose_name = "后台账号"
        verbose_name_plural = "后台账号"
