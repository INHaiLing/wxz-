import django.core.validators
import resources.models
import resources.storage
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('resources', '0002_groupresourceconfiguration_resources_group_config_singleton'),
    ]

    operations = [
        migrations.AlterField(
            model_name='groupresourceconfiguration',
            name='group_qr_code',
            field=models.FileField(blank=True, storage=resources.storage.PublicGroupQRStorage(), upload_to=resources.models.group_qr_upload_to, validators=[django.core.validators.FileExtensionValidator(allowed_extensions=('png', 'jpg', 'jpeg', 'webp')), resources.models.validate_qr_image], verbose_name='微信群二维码'),
        ),
    ]
