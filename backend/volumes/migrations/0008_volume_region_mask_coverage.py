from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("volumes", "0007_volume_file_format_nifti")]

    operations = [
        migrations.AddField(
            model_name="volume",
            name="region_mask_coverage",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
