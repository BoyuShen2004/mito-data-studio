"""Give existing projects a real Dataset and attach their volumes to it.

Before this, a project *was* a dataset: the name lived in ``Project.dataset``
and its metadata on the project. Datasets are now first-class, so each project
that has data gets one dataset carrying that name and metadata, and every
volume is pointed at it. Projects keep their ``dataset`` string in step.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    Dataset = apps.get_model("projects", "Dataset")
    Volume = apps.get_model("volumes", "Volume")

    for project in Project.objects.all():
        volumes = Volume.objects.filter(project=project, dataset__isnull=True)
        # A project with no data and no dataset name has nothing to migrate.
        if not volumes.exists() and not (project.dataset or "").strip():
            continue

        name = (project.dataset or "").strip() or project.title or f"dataset-{project.pk}"
        # Volumes registered together recorded their source directories; reuse
        # the first as the dataset's provenance.
        first = volumes.first()
        dataset, _ = Dataset.objects.get_or_create(
            project=project,
            name=name,
            defaults={
                "description": project.description or "",
                "metadata": project.metadata or {},
                "image_directory": _parent_of(getattr(first, "image_path", "")),
                "mask_directory": _parent_of(getattr(first, "label_path", "")),
            },
        )
        volumes.update(dataset=dataset)


def _parent_of(path: str) -> str:
    if not path:
        return ""
    head, sep, _ = str(path).rpartition("/")
    return head if sep else ""


def backwards(apps, schema_editor):
    # Volumes simply lose their dataset link; the names remain on Project.
    Volume = apps.get_model("volumes", "Volume")
    Volume.objects.update(dataset=None)


class Migration(migrations.Migration):

    dependencies = [
        ("volumes", "0003_volume_dataset"),
        ("projects", "0005_dataset"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
