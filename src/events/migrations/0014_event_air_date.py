from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("events", "0013_delete_single_anime_events")]

    operations = [
        migrations.AddField(
            model_name="event",
            name="air_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
