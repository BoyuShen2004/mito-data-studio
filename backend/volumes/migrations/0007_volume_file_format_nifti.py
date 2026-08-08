from django.db import migrations, models


def classify_nifti(apps, schema_editor):
    Volume = apps.get_model("volumes", "Volume")
    for volume in Volume.objects.filter(file_format="other").only("id", "image_path", "image_file"):
        location = volume.image_path or str(volume.image_file or "")
        if location.lower().endswith((".nii", ".nii.gz")):
            Volume.objects.filter(pk=volume.pk).update(file_format="nifti")


class Migration(migrations.Migration):
    dependencies = [("volumes", "0006_volume_region_mask")]
    operations = [
        migrations.AlterField(
            model_name="volume",
            name="file_format",
            field=models.CharField(
                choices=[("tiff", "TIFF"), ("zarr", "Zarr"), ("hdf5", "HDF5"), ("nifti", "NIfTI"), ("n5", "N5"), ("other", "Other")],
                default="tiff",
                max_length=10,
            ),
        ),
        migrations.RunPython(classify_nifti, migrations.RunPython.noop),
    ]
