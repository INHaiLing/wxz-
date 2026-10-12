from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('resources', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='groupresourceconfiguration',
            constraint=models.CheckConstraint(condition=models.Q(('pk', 1)), name='resources_group_config_singleton'),
        ),
    ]
