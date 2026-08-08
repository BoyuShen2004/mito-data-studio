"""Reconcile tasks created after the original hierarchy backfill.

v1.0 kept the hierarchy feature disabled, so tasks created after migration 0008
continued to use only the legacy ``assigned_to`` fields. Before v1.1 enables
claiming, materialize their blueprint and occupied instance exactly once.
"""

from django.db import migrations


TASK_KIND_LABELS = {
    "manual_annotation": "Manual annotation",
    "prediction_proofreading": "Prediction proofreading",
    "final_review": "Final review",
    "qc_review": "QC review",
}
STATUS_TO_INSTANCE_STATE = {
    "assigned": "claimed",
    "in_progress": "in_progress",
    "submitted": "submitted",
    "approved": "completed",
    "rejected": "in_progress",
    "revision_requested": "in_progress",
}
COUNTING = {"claimed", "in_progress", "submitted", "completed"}


def reconcile_late_tasks(apps, schema_editor):
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
        blueprint = task.task_type_ref
        if blueprint is None:
            blueprint = blueprint_for(
                task.task_type,
                getattr(task.project, "institution_id", None),
            )

        if task.assigned_to_id:
            TaskInstance.objects.get_or_create(
                task_id=task.pk,
                assigned_to_id=task.assigned_to_id,
                defaults={
                    "state": STATUS_TO_INSTANCE_STATE.get(task.status, "claimed"),
                    "claimed_at": task.assigned_at,
                    "submitted_at": task.submitted_at,
                    "completed_at": task.approved_at,
                },
            )

        occupied = TaskInstance.objects.filter(
            task_id=task.pk,
            state__in=COUNTING,
        ).count()
        total = max(task.total_instances, occupied, 1)
        AnnotationTask.objects.filter(pk=task.pk).update(
            task_type_ref_id=blueprint.pk,
            total_instances=total,
            pending_instances=max(total - occupied, 0),
        )


class Migration(migrations.Migration):
    dependencies = [("annotation", "0012_annotation_operations")]
    operations = [
        migrations.RunPython(reconcile_late_tasks, migrations.RunPython.noop),
    ]
