import re

from django.db import migrations


KEY = "working_mask_basename"
MAX_NAME_LEN = 80


def _basename(location):
    return re.split(r"[\\/]", location or "")[-1]


def _safe_name(value, fallback):
    value = re.sub(r"[\\/\x00-\x1f]", "-", value.strip())
    value = value.strip(" .")[:MAX_NAME_LEN].strip(" .")
    return value if value and value not in {".", ".."} else fallback


def _image_stem(volume):
    fallback = f"volume_{volume.id}"
    name = _basename(volume.image_file or volume.image_path)
    if not name:
        return fallback
    low = name.lower()
    for ext in (".nii.gz", ".tiff", ".tif", ".hdf5", ".h5", ".nii"):
        if low.endswith(ext):
            name = name[: -len(ext)]
            break
    stem = _safe_name(name, fallback)
    if stem.lower().endswith("_mask") and len(stem) > len("_mask"):
        stem = stem[: -len("_mask")]
    return stem


def pin_existing_names(apps, schema_editor):
    Volume = apps.get_model("volumes", "Volume")
    seen = set()
    for volume in Volume.objects.order_by("id").iterator():
        metadata = dict(volume.metadata or {})
        if metadata.get(KEY):
            continue
        stem = _image_stem(volume)
        scope = (volume.project_id, volume.dataset_id, stem)
        basename = f"{stem}_mask.tif" if scope not in seen else f"{stem}_v{volume.id}_mask.tif"
        seen.add(scope)
        metadata[KEY] = basename
        Volume.objects.filter(pk=volume.pk).update(metadata=metadata)


class Migration(migrations.Migration):
    dependencies = [("volumes", "0012_unique_registered_image_per_dataset")]
    operations = [migrations.RunPython(pin_existing_names, migrations.RunPython.noop)]
