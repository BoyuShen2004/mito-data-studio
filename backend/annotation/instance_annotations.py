"""Per-instance morphology and QA flags.

One write point, :func:`set_instance_annotation`, so the delete-when-empty
rule and the audit write cannot be forgotten by a caller.

The central invariant: **an absent row means "not annotated"**. Every existing
volume is therefore valid with zero rows here, nothing backfills the table, and
a row that carries no information is deleted rather than stored — so "has a
row" always means "somebody recorded something". Reading a blank morphology as
``normal`` anywhere would destroy that distinction, which is the difference
between "nobody looked" and "somebody looked and found nothing unusual".
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q

from accounts.audit import record_audit
from core.choices import AuditVerb, InstanceQaFlag, MitoMorphology

from .models import LabelInstanceAnnotation


def instance_annotation_enabled() -> bool:
    """Is the per-instance annotation surface turned on for this deployment?"""
    return bool(getattr(settings, "FEATURE_INSTANCE_ANNOTATION", False))


VALID_MORPHOLOGIES = {choice.value for choice in MitoMorphology}
VALID_QA_FLAGS = {choice.value for choice in InstanceQaFlag}


def normalize_qa_flags(raw) -> list[str]:
    """Validated, de-duplicated, enum-ordered QA flags.

    Order comes from the enum rather than from input order so two clients that
    tick the same boxes in a different sequence store identical rows — which is
    what makes an equality check on this field meaningful.

    Raises ``ValueError`` on anything outside :class:`InstanceQaFlag`; an
    unknown flag is a client bug and must not be silently dropped, because a
    dropped flag reads downstream as "the annotator did not raise it".
    """
    if raw is None:
        return []
    if isinstance(raw, str) or not hasattr(raw, "__iter__"):
        raise ValueError("qa_flags must be a list of flag values")
    seen = set()
    for value in raw:
        if value not in VALID_QA_FLAGS:
            raise ValueError(f"Unknown QA flag: {value!r}")
        seen.add(value)
    return [choice.value for choice in InstanceQaFlag if choice.value in seen]


def normalize_morphology(raw) -> str:
    """Validated morphology, or ``""`` for "unclassified"."""
    if raw is None or raw == "":
        return ""
    if raw not in VALID_MORPHOLOGIES:
        raise ValueError(f"Unknown morphology: {raw!r}")
    return raw


@transaction.atomic
def set_instance_annotation(
    volume,
    label_id: int,
    *,
    actor=None,
    morphology=None,
    qa_flags=None,
    note=None,
) -> LabelInstanceAnnotation | None:
    """Upsert one instance's annotation. Returns the row, or ``None`` if empty.

    A field left at ``None`` is **not changed**; pass ``""`` / ``[]`` to clear
    one. That distinction matters because the panel sends partial updates as
    the user edits one control at a time, and treating "not sent" as "clear"
    would erase the other two fields on every keystroke.

    When the result carries no information the row is deleted, keeping the
    table free of rows that mean nothing.
    """
    if label_id is None or int(label_id) <= 0:
        # 0 is background in every label raster this app writes; annotating it
        # is always a client bug rather than a user intent.
        raise ValueError("label_id must be a positive instance id")
    label_id = int(label_id)

    row = (
        LabelInstanceAnnotation.objects.select_for_update()
        .filter(volume=volume, label_id=label_id)
        .first()
    )
    current_morphology = row.morphology if row else ""
    current_flags = list(row.qa_flags or []) if row else []
    current_note = row.note if row else ""

    new_morphology = (
        current_morphology if morphology is None else normalize_morphology(morphology)
    )
    new_flags = current_flags if qa_flags is None else normalize_qa_flags(qa_flags)
    new_note = current_note if note is None else str(note)[:280]

    if not new_morphology and not new_flags and not new_note:
        if row is not None:
            row.delete()
            record_audit(
                actor,
                AuditVerb.INSTANCE_ANNOTATION_CLEARED,
                target=volume,
                label_id=label_id,
            )
        return None

    if row is None:
        row = LabelInstanceAnnotation(volume=volume, label_id=label_id)
    row.morphology = new_morphology
    row.qa_flags = new_flags
    row.note = new_note
    row.updated_by = actor if getattr(actor, "is_authenticated", False) else None
    row.save()

    record_audit(
        actor,
        AuditVerb.INSTANCE_ANNOTATION_SET,
        target=volume,
        label_id=label_id,
        morphology=new_morphology,
        qa_flags=new_flags,
    )
    return row


def annotations_for_volume(volume, *, label_ids=None):
    """Every annotated instance in one volume, optionally narrowed to ids."""
    queryset = LabelInstanceAnnotation.objects.filter(volume=volume).select_related(
        "updated_by"
    )
    if label_ids is not None:
        queryset = queryset.filter(label_id__in=list(label_ids))
    return queryset


def flagged_for_task(task):
    """Instances a reviewer should look at, for one task's volume.

    Deliberately volume-scoped rather than task-scoped: instance ids are a
    property of the volume's raster and this app has no cheap way to know which
    z-range an instance occupies without reading voxels. The reviewer's list
    would rather be complete than narrow.
    """
    from core.choices import REVIEW_WORTHY_QA_FLAGS

    worthy = [flag.value for flag in REVIEW_WORTHY_QA_FLAGS]
    queryset = LabelInstanceAnnotation.objects.filter(volume=task.volume)
    # JSONField containment is not portable across SQLite and PostgreSQL, so
    # the flag test is done in Python over an already-small result set — this
    # is the annotated instances of one volume, not a row-per-voxel scan.
    return [row for row in queryset if any(flag in worthy for flag in (row.qa_flags or []))]


def project_summary(project) -> dict:
    """Morphology and flag counts across one project.

    The morphology half is **one grouped query**, per the rule in
    ``core.statistics``. The flag half cannot be grouped in the database — the
    values live inside a JSON list and the app supports both SQLite and
    PostgreSQL — so it is counted in Python over the annotated rows of one
    project, which is bounded by "instances somebody bothered to annotate",
    not by voxel or task count.
    """
    base = LabelInstanceAnnotation.objects.filter(volume__project=project)

    morphology = {choice.value: 0 for choice in MitoMorphology}
    unclassified = 0
    for row in base.values("morphology").annotate(n=Count("id")):
        if row["morphology"]:
            morphology[row["morphology"]] = row["n"]
        else:
            unclassified = row["n"]

    flags = {choice.value: 0 for choice in InstanceQaFlag}
    for values in base.values_list("qa_flags", flat=True):
        for flag in values or []:
            if flag in flags:
                flags[flag] += 1

    return {
        "total_annotated": base.count(),
        "morphology": morphology,
        # Instances carrying a flag or a note but no phenotype. Reported
        # separately so a reader never mistakes it for a MitoMorphology value.
        "morphology_unclassified": unclassified,
        "qa_flags": flags,
    }


def vocabulary() -> dict:
    """The fixed vocabulary, served to the client so it is defined once.

    The frontend renders whatever this returns rather than hard-coding its own
    copy of the enum, so adding a phenotype is a backend-only change.
    """
    return {
        "morphology": [
            {"value": choice.value, "label": choice.label} for choice in MitoMorphology
        ],
        "qa_flags": [
            {"value": choice.value, "label": choice.label} for choice in InstanceQaFlag
        ],
    }
