from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("volumes", "0011_remove_volume_chunk_source_grouping")]

    operations = [
        migrations.AddConstraint(
            model_name="volume",
            constraint=models.UniqueConstraint(
                fields=("dataset", "image_path"),
                condition=Q(dataset__isnull=False) & ~Q(image_path=""),
                name="unique_registered_image_per_dataset",
            ),
        )
    ]
