import django.core.validators
import django.db.models.deletion
import resources.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='GroupResourceConfiguration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('group_wechat_id', models.CharField(blank=True, max_length=128, verbose_name='微信号')),
                ('group_qr_code', models.FileField(blank=True, upload_to=resources.models.group_qr_upload_to, validators=[django.core.validators.FileExtensionValidator(allowed_extensions=('png', 'jpg', 'jpeg', 'webp')), resources.models.validate_qr_image], verbose_name='微信群二维码')),
                ('claim_code', models.CharField(blank=True, max_length=160, verbose_name='领取口令')),
                ('description', models.TextField(blank=True, verbose_name='资料说明')),
                ('announcement', models.TextField(blank=True, verbose_name='群公告')),
                ('is_enabled', models.BooleanField(default=False, verbose_name='启用')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='修改时间')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='updated_group_resource_configurations', to=settings.AUTH_USER_MODEL, verbose_name='最后修改人')),
            ],
            options={
                'verbose_name': '资料群配置',
                'verbose_name_plural': '资料群配置',
                'default_permissions': ('view', 'add', 'change', 'delete'),
            },
        ),
    ]
