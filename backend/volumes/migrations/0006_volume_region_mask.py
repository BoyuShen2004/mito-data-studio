import core.storage
from django.db import migrations, models
import volumes.models


class Migration(migrations.Migration):
    dependencies = [("volumes", "0005_phase11_volume_pyramid")]
    operations = [
        migrations.AddField(
            model_name="volume",
            name="region_mask_file",
            field=models.FileField(
                blank=True,
                null=True,
                storage=core.storage.get_mito_storage,
                upload_to=volumes.models.volume_region_mask_upload_to,
            ),
        ),
        migrations.AddField(
            model_name="volume",
            name="region_mask_path",
            field=models.CharField(blank=True, max_length=1024),
        ),
    ]
