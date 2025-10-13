# Generated manually for adding runtime field to Item model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0052_add_air_date_to_item'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='runtime',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]