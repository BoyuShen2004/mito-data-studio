"""Turning two label volumes into a stored :class:`QualityScore`.

Two sources of a trustworthy comparison, and deliberately no third:

* **Gold standard** — the volume carries ``is_gold_standard`` and points at a
  ``reference_submission`` that already passed review. The annotator is not
  told; a known test measures attention rather than ordinary accuracy.
* **Reviewer agreement** — when a reviewer approves a submission *after
  editing it*, the difference between what was submitted and what was approved
  is the error signal, and it is already on disk. This costs no extra
  annotation, which is why this module never asks two people to label the same
  voxels twice.

Scoring is always **best-effort**. A metric failure must never fail the submit
or the approval that triggered it — the annotator's work is already durable by
the time we get here, and losing a number is strictly better than losing an
approval.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction

from accounts.audit import record_audit
from core.choices import AuditVerb, QualityScoreKind

from .models import QualityScore
from .quality_metrics import compare

logger = logging.getLogger(__name__)

PROVIDER_NAME = "overlap"

# Read this many z-slices at a time. Large enough that the per-slab numpy
# overhead is amortised, small enough that two slabs of a 4k x 4k volume stay
# well under a hundred megabytes.
SLAB_DEPTH = 8


def quality_metrics_enabled() -> bool:
    return bool(getattr(settings, "FEATURE_QUALITY_METRICS", False))


def _submission_array(submission):
    """The label array a submission stands for, or ``None`` if unreadable.

    Both channels own a file: an uploaded one for offline submissions, and an
    immutable snapshot copied from the working label for in-app ones (see
    ``services.submit_inapp_annotation``). Neither points at a mutable draft,
    which is what makes a score computed against it stable.
    """
    import numpy as np

    field = getattr(submission, "label_file", None)
    if not field or not field.name:
        return None
    try:
        path = field.storage.path(field.name)
    except (NotImplementedError, ValueError):
        logger.warning("Storage for submission %s exposes no path", submission.pk)
        return None

    from .visualization.hdf5_io import is_hdf5_path, open_hdf5_volume
    from .visualization.nifti_io import is_nifti_path, open_nifti_volume
    from pathlib import Path

    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        if is_hdf5_path(file_path):
            view = open_hdf5_volume(file_path)
            try:
                return np.asarray(view)
            finally:
                view.close()
        if is_nifti_path(file_path):
            return np.asarray(open_nifti_volume(file_path))
        import tifffile

        return np.asarray(tifffile.imread(str(file_path)))
    except Exception:  # noqa: BLE001 - an unreadable label means "no score"
        logger.exception("Could not read label for submission %s", submission.pk)
        return None


def _slab_pairs(candidate, reference):
    """Yield matching z-slabs, so neither volume is ever held twice at once."""
    depth = candidate.shape[0] if candidate.ndim == 3 else 1
    if candidate.ndim != 3:
        yield candidate, reference
        return
    for start in range(0, depth, SLAB_DEPTH):
        stop = min(start + SLAB_DEPTH, depth)
        yield candidate[start:stop], reference[start:stop]


def score_submission(
    submission,
    reference_submission,
    *,
    kind,
    actor=None,
) -> QualityScore | None:
    """Compare one submission against a reference and store the result.

    Returns ``None`` — never a row of zeros — when either side is unreadable
    or the two disagree about shape. A shape mismatch is a real condition (the
    reference was cut from a different volume) and recording it as "0.0 Dice"
    would libel the annotator.
    """
    if submission is None or reference_submission is None:
        return None
    if submission.pk == reference_submission.pk:
        return None

    candidate = _submission_array(submission)
    reference = _submission_array(reference_submission)
    if candidate is None or reference is None:
        return None
    if candidate.shape != reference.shape:
        logger.warning(
            "Shape mismatch scoring submission %s against %s: %s vs %s",
            submission.pk,
            reference_submission.pk,
            candidate.shape,
            reference.shape,
        )
        return None

    try:
        metrics = compare(_slab_pairs(candidate, reference))
    except Exception:  # noqa: BLE001 - a metric failure must not fail the caller
        logger.exception("Could not score submission %s", submission.pk)
        return None

    score = QualityScore.objects.create(
        submission=submission,
        kind=kind,
        reference_submission=reference_submission,
        dice=metrics.get("dice"),
        iou=metrics.get("iou"),
        precision=metrics.get("precision"),
        recall=metrics.get("recall"),
        instance_f1=metrics.get("instance_f1"),
        false_merges=metrics.get("false_merges"),
        false_splits=metrics.get("false_splits"),
        variation_of_information=metrics.get("variation_of_information"),
        provider=PROVIDER_NAME,
        detail={
            "matched": metrics.get("matched"),
            "candidate_instances": metrics.get("candidate_instances"),
            "reference_instances": metrics.get("reference_instances"),
        },
    )
    record_audit(
        actor,
        AuditVerb.QUALITY_SCORED,
        target=submission,
        kind=str(kind),
        dice=metrics.get("dice"),
    )
    refresh_annotator_quality(submission.annotator)
    return score


def score_against_gold_standard(submission, *, actor=None) -> QualityScore | None:
    """Score a submission if — and only if — its volume is a gold standard."""
    if not quality_metrics_enabled():
        return None
    volume = submission.task.volume
    if not volume.is_gold_standard or volume.reference_submission_id is None:
        return None
    return score_submission(
        submission,
        volume.reference_submission,
        kind=QualityScoreKind.GOLD_STANDARD,
        actor=actor,
    )


def score_reviewer_agreement(
    submission, approved_submission, *, actor=None
) -> QualityScore | None:
    """How much correction the reviewer had to apply to this submission."""
    if not quality_metrics_enabled():
        return None
    return score_submission(
        submission,
        approved_submission,
        kind=QualityScoreKind.REVIEWER_AGREEMENT,
        actor=actor,
    )


@transaction.atomic
def refresh_annotator_quality(user) -> float | None:
    """Recompute one annotator's rolling quality score. Returns it, or ``None``.

    The number is the mean gold-standard Dice over their most recent
    ``MITO_QUALITY_SCORE_WINDOW`` scored submissions. Reviewer-agreement scores
    are excluded on purpose: they measure how much a reviewer chose to change,
    which mixes the reviewer's own standards into a figure that is supposed to
    describe the annotator.

    **A person with no scored submissions gets ``None``, not ``0.0``.** The
    stored column keeps ``0.0`` as its historical default, so callers must read
    this function's return value — or the serializer field derived from it —
    rather than the raw column, whenever the difference between "not measured"
    and "measured badly" matters.
    """
    if user is None or not getattr(user, "pk", None):
        return None

    window = int(getattr(settings, "MITO_QUALITY_SCORE_WINDOW", 10))
    recent = list(
        QualityScore.objects.filter(
            submission__annotator=user,
            kind=QualityScoreKind.GOLD_STANDARD,
            dice__isnull=False,
        )
        .order_by("-computed_at", "-id")
        .values_list("dice", flat=True)[:window]
    )
    if not recent:
        return None

    mean = sum(recent) / len(recent)
    from accounts.models import AnnotatorProfile

    AnnotatorProfile.objects.filter(user=user).update(quality_score=mean)
    return mean


def annotator_quality(user) -> float | None:
    """This person's rolling quality score, or ``None`` if never measured.

    Reads the scores rather than the denormalized column so "never measured"
    stays distinguishable from "measured as zero" at every call site.
    """
    if user is None or not getattr(user, "pk", None):
        return None
    window = int(getattr(settings, "MITO_QUALITY_SCORE_WINDOW", 10))
    recent = list(
        QualityScore.objects.filter(
            submission__annotator=user,
            kind=QualityScoreKind.GOLD_STANDARD,
            dice__isnull=False,
        )
        .order_by("-computed_at", "-id")
        .values_list("dice", flat=True)[:window]
    )
    return (sum(recent) / len(recent)) if recent else None
