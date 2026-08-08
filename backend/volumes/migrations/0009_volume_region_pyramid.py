from django.db import migrations, models


class Migration(migrations.Migration):
    """Readiness for the region-mask derivative (ADR-009 addendum).

    Additive and independent of the image pyramid: an existing volume keeps its
    ``ready_streaming`` exactly as it is and starts with no region derivative,
    which is an accurate absence rather than a backfill debt.
    """

    dependencies = [
        ("volumes", "0008_volume_region_mask_coverage"),
    ]

    operations = [
        migrations.AddField(
            model_name="volume",
            name="region_ready_streaming",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "A validated Zarr v3 pyramid exists for this volume's region "
                    "mask. Flipped only after random-chunk checksums pass."
                ),
            ),
        ),
        migrations.AddField(
            model_name="volume",
            name="region_pyramid_metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
