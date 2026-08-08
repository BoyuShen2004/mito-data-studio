from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("projects", "0007_project_paused_project_priority")]
    operations = [
        migrations.AddField(
            model_name="dataset",
            name="region_mask_directory",
            field=models.CharField(blank=True, max_length=1024),
        )
    ]
