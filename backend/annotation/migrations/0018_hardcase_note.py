from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("annotation", "0017_dual_submission_channels")]

    operations = [
        migrations.AddField(
            model_name="hardcase",
            name="note",
            field=models.TextField(blank=True, max_length=1000),
        ),
    ]
