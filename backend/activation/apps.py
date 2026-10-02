from django.apps import AppConfig


class ActivationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "activation"
    verbose_name = "永久激活码"
