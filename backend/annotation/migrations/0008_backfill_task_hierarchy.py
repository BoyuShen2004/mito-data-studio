"""Phase 2 backfill: express every existing task in the new hierarchy.

Additive and reversible. For each existing ``AnnotationTask``:

* point ``task_type_ref`` at a ``TaskType`` blueprint derived from its
  ``task_type`` enum (one blueprint per enum value per organisation);
* give it ``total_instances = 1`` — the legacy model is exactly "one pass";
* materialise a ``TaskInstance`` for its ``assigned_to``, if it has one, in the
  state matching the task's current status;
* set ``pending_instances`` to 1 or 0 accordingly.

Nothing is read *from* the new columns until FEATURE_TASK_HIERARCHY is on, so
this is safe to apply well ahead of any behaviour change. The reverse drops
only what this created, leaving the legacy columns untouched.
"""

from django.db import migrations

# Local copies — migrations must not import from core.choices, which is free to
# change shape later.
TASK_KIND_LABELS = {
    "manual_annotation": "Manual annotation",
    "prediction_proofreading": "Prediction proofreading",
    "final_review": "Final review",
    "qc_review": "QC review",
}

# AnnotationTask.status -> TaskInstance.state for the backfilled instance.
STATUS_TO_INSTANCE_STATE = {
    "assigned": "claimed",
    "in_progress": "in_progress",
    "submitted": "submitted",
    "approved": "completed",
    "rejected": "in_progress",           # back with the annotator
    "revision_requested": "in_progress",  # likewise
}

# States that occupy one of a task's slots (mirrors COUNTING_INSTANCE_STATES).
COUNTING = {"claimed", "in_progress", "submitted", "completed"}


def backfill(apps, schema_editor):
    AnnotationTask = apps.get_model("annotation", "AnnotationTask")
    TaskType = apps.get_model("annotation", "TaskType")
    TaskInstance = apps.get_model("annotation", "TaskInstance")

    blueprints = {}

    def blueprint_for(kind, organization_id):
        key = (kind, organization_id)
        if key not in blueprints:
            blueprints[key], _ = TaskType.objects.get_or_create(
                organization_id=organization_id,
                name=TASK_KIND_LABELS.get(kind, kind),
                defaults={
                    "legacy_kind": kind,
                    "description": "Created automatically from the task_type enum.",
                    "settings": {},
                },
            )
        return blueprints[key]

    tasks = AnnotationTask.objects.select_related("project").all()
    for task in tasks.iterator(chunk_size=500):
        org_id = getattr(task.project, "institution_id", None)
        blueprint = blueprint_for(task.task_type, org_id)

        occupied = 0
        if task.assigned_to_id:
            state = STATUS_TO_INSTANCE_STATE.get(task.status, "claimed")
            TaskInstance.objects.get_or_create(
                task=task,
                assigned_to_id=task.assigned_to_id,
                defaults={
                    "state": state,
                    "claimed_at": task.assigned_at,
                    "submitted_at": task.submitted_at,
                    "completed_at": task.approved_at,
                },
            )
            if state in COUNTING:
                occupied = 1

        AnnotationTask.objects.filter(pk=task.pk).update(
            task_type_ref=blueprint,
            total_instances=1,
            pending_instances=max(1 - occupied, 0),
        )


def unbackfill(apps, schema_editor):
    """Remove exactly what `backfill` created."""
    AnnotationTask = apps.get_model("annotation", "AnnotationTask")
    TaskType = apps.get_model("annotation", "TaskType")
    TaskInstance = apps.get_model("annotation", "TaskInstance")

    TaskInstance.objects.all().delete()
    AnnotationTask.objects.update(
        task_type_ref=None, total_instances=1, pending_instances=1
    )
    TaskType.objects.filter(
        description="Created automatically from the task_type enum."
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("annotation", "0007_annotationtask_pending_instances_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
