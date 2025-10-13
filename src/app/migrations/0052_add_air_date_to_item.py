# Generated manually for adding air_date field to Item model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0051_migrate_simkl_periodoc_tasks'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='air_date',
            field=models.DateField(blank=True, null=True),
        ),
    ]