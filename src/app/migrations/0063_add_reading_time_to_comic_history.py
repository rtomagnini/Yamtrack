# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0062_historicalseason_broadcast_time'),
    ]

    operations = [
        migrations.AddField(
            model_name='comic',
            name='reading_time',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Reading time in minutes for this issue (Comics only)'
            ),
        ),
        migrations.AddField(
            model_name='historicalcomic',
            name='reading_time',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Reading time in minutes for this issue (Comics only)'
            ),
        ),
    ]
