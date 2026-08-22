"""Rollout classification for automatic annotation time tracking.

The feature is deliberately **prospective**. A volume whose annotation was
already under way when it shipped has an unknowable real total: we would be
able to measure only the work done from today onward, and showing that as
"the time this volume took" would be a smaller number presented as a complete
one — worse than admitting we do not know.

So, once, at rollout:

* a volume that is **currently assigned to an annotator** is ``LEGACY_EXEMPT``
  and reports ``-`` forever;
* a volume that is **not assigned** is ``ELIGIBLE``: nothing has been annotated
  unmeasured, so it starts accumulating the moment it is assigned and opened;
* volumes registered afterwards are ``ELIGIBLE`` by the model default.

"Assigned" uses the repository's real definition — ``AnnotationTask.assigned_to``
on any task covering the volume — rather than anything on ``Volume`` itself,
because assignment lives on the task and always has.

Idempotent by construction: only rows that have never been classified
(``time_tracking_set_at IS NULL``) are touched, so re-running after a partial
apply cannot reclassify a volume whose eligibility someone has since changed
on purpose. Reversing restores the unclassified state rather than guessing.
"""

from django.db import migrations
from django.utils import timezone

ELIGIBLE = "eligible"
LEGACY_EXEMPT = "legacy_exempt"

REASON_ASSIGNED = "rollout_assigned_at_launch"
REASON_UNASSIGNED = "rollout_unassigned_at_launch"


def classify_existing_volumes(apps, schema_editor):
    Volume = apps.get_model("volumes", "Volume")
    AnnotationTask = apps.get_model("annotation", "AnnotationTask")

    now = timezone.now()
    unclassified = Volume.objects.filter(time_tracking_set_at__isnull=True)

    # One query for the whole "which volumes are assigned?" question. Doing it
    # per volume would be an N+1 against every volume in the installation,
    # which on a real deployment is exactly when a migration must not be slow.
    assigned_volume_ids = set(
        AnnotationTask.objects.filter(assigned_to__isnull=False)
        .values_list("volume_id", flat=True)
        .distinct()
    )

    unclassified.filter(id__in=assigned_volume_ids).update(
        time_tracking=LEGACY_EXEMPT,
        time_tracking_set_at=now,
        time_tracking_reason=REASON_ASSIGNED,
    )
    unclassified.exclude(id__in=assigned_volume_ids).update(
        time_tracking=ELIGIBLE,
        time_tracking_set_at=now,
        time_tracking_reason=REASON_UNASSIGNED,
    )


def unclassify(apps, schema_editor):
    """Undo the rollout stamp only for rows this migration itself set.

    Eligibility changed by hand afterwards carries a different reason and is
    left alone — a reverse migration must not quietly discard an administrative
    decision.
    """
    Volume = apps.get_model("volumes", "Volume")
    Volume.objects.filter(
        time_tracking_reason__in=[REASON_ASSIGNED, REASON_UNASSIGNED]
    ).update(time_tracking=ELIGIBLE, time_tracking_set_at=None, time_tracking_reason="")


class Migration(migrations.Migration):

    dependencies = [
        ("volumes", "0014_volume_time_tracking"),
        # The classification reads task assignment, so the task table must
        # exist in the state this migration runs against.
        ("annotation", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(classify_existing_volumes, unclassify),
    ]
