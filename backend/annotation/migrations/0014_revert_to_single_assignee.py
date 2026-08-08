"""Collapse redundant task instances back to the single-assignee product.

TaskInstance remains as an internal lifecycle/audit primitive, but every task
has one slot.  Preserve the legacy ``assigned_to`` holder when possible;
otherwise keep the most advanced live holder and cancel the redundant rows so
no second annotator can continue holding the same task.
"""

from django.db import migrations


COUNTING_STATES = ("claimed", "in_progress", "submitted", "completed")
STATE_PRIORITY = {
    "completed": 0,
    "submitted": 1,
    "in_progress": 2,
    "claimed": 3,
}
TASK_TO_INSTANCE_STATE = {
    "assigned": "claimed",
    "in_progress": "in_progress",
    "submitted": "submitted",
    "approved": "completed",
    "rejected": "in_progress",
    "revision_requested": "in_progress",
}
INSTANCE_TO_TASK_STATUS = {
    "claimed": "assigned",
    "in_progress": "in_progress",
    "submitted": "submitted",
    "completed": "approved",
}


def clamp_to_single_assignee(apps, schema_editor):
    AnnotationTask = apps.get_model("annotation", "AnnotationTask")
    TaskInstance = apps.get_model("annotation", "TaskInstance")

    for task in AnnotationTask.objects.all().iterator(chunk_size=500):
        live = list(
            TaskInstance.objects.filter(
                task_id=task.pk,
                state__in=COUNTING_STATES,
                assigned_to_id__isnull=False,
            )
        )

        keep = None
        if task.assigned_to_id:
            keep = next(
                (row for row in live if row.assigned_to_id == task.assigned_to_id),
                None,
            )
            if keep is None:
                # A late legacy task may have an assignee but only a cancelled
                # lifecycle row. Reuse it because the existing uniqueness
                # constraint permits only one row per task/user.
                keep = TaskInstance.objects.filter(
                    task_id=task.pk,
                    assigned_to_id=task.assigned_to_id,
                ).first()
                target_state = TASK_TO_INSTANCE_STATE.get(task.status, "claimed")
                if keep is None:
                    keep = TaskInstance.objects.create(
                        task_id=task.pk,
                        assigned_to_id=task.assigned_to_id,
                        state=target_state,
                        claimed_at=task.assigned_at,
                        submitted_at=task.submitted_at,
                        completed_at=task.approved_at,
                    )
                else:
                    keep.state = target_state
                    keep.lease_expires_at = None
                    keep.heartbeat_at = None
                    keep.save(
                        update_fields=["state", "lease_expires_at", "heartbeat_at"]
                    )
                live.append(keep)
        elif live:
            keep = min(
                live,
                key=lambda row: (STATE_PRIORITY.get(row.state, 99), row.pk),
            )

        keep_id = keep.pk if keep is not None else None
        redundant = TaskInstance.objects.filter(
            task_id=task.pk,
            state__in=COUNTING_STATES,
        )
        if keep_id is not None:
            redundant = redundant.exclude(pk=keep_id)
        redundant.update(
            state="cancelled",
            lease_expires_at=None,
            heartbeat_at=None,
        )

        assignee_id = keep.assigned_to_id if keep is not None else None
        updates = {
            "total_instances": 1,
            "pending_instances": 0 if assignee_id else 1,
        }
        if assignee_id != task.assigned_to_id:
            updates["assigned_to_id"] = assignee_id
            updates["status"] = INSTANCE_TO_TASK_STATUS.get(
                keep.state if keep is not None else "", "unassigned"
            )
            updates["assigned_at"] = (
                keep.claimed_at if keep is not None else None
            )
        AnnotationTask.objects.filter(pk=task.pk).update(**updates)


class Migration(migrations.Migration):
    dependencies = [("annotation", "0013_backfill_late_legacy_tasks")]
    operations = [
        migrations.RunPython(clamp_to_single_assignee, migrations.RunPython.noop),
    ]
