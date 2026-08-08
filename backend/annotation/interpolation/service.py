"""Phase 8 — interpolation orchestration.

Separates the seven concerns the brief names, so none of them lives in the
mathematics: input validation, source loading, computation, output validation,
materialized-state update, Phase 7 operation recording, and error reporting.

The core (``core.py``) stays pure. This module is the only part that knows
about Django, the working label volume, or the operation log.

Two entry points, which together are the backend half of §E8's
"Interpolate -> preview -> confirm/cancel":

* :func:`plan_interpolation` computes and **writes nothing**;
* :func:`apply_interpolation` commits, in one transaction, recording exactly
  one Phase 7 operation.
"""

from __future__ import annotations

import logging

import numpy as np
from django.conf import settings
from django.db import transaction

from annotation.models import AnnotationOperation, AnnotationTask
from annotation.operations import (
    OperationError,
    append_operation,
    current_version,
    operations_enabled,
)

from . import core
from .core import InterpolationError, InterpolationPlan

logger = logging.getLogger(__name__)


def interpolation_enabled() -> bool:
    """Both flags: applying an interpolation records a Phase 7 operation."""
    return bool(getattr(settings, "FEATURE_INTERPOLATION", False))


def _require_enabled() -> None:
    if not interpolation_enabled():
        raise InterpolationError(
            "Interpolation is disabled (FEATURE_INTERPOLATION).",
            reason="disabled",
        )


def _max_voxels() -> int:
    return int(getattr(settings, "MITO_INTERPOLATION_MAX_VOXELS",
                       core.DEFAULT_MAX_VOXELS))


def plan_interpolation(*, first_labels, last_labels, label_id: int, depth: int,
                       spacing=(1.0, 1.0),
                       overwrite_mode: str = core.OVERWRITE_EMPTY
                       ) -> InterpolationPlan:
    """Compute the proposed intermediate masks. Writes nothing, touches no row.

    This is the preview: a caller can render or discard the result without
    having modified stored labels, which is what makes "confirm/cancel"
    possible at all.
    """
    _require_enabled()
    return core.plan(
        first_labels, last_labels, label_id=label_id, depth=depth,
        spacing=spacing, overwrite_mode=overwrite_mode,
        max_voxels=_max_voxels(),
    )


def _payload_for(plan: InterpolationPlan, *, axis: str, first_index: int,
                 last_index: int, source_version: int, bbox=None) -> dict:
    """What the operation records. Metadata only — never voxels.

    Enough to understand and reproduce the call; small enough to stay well
    inside Phase 7's 16 KiB payload cap regardless of how large the
    interpolated region was.
    """
    return {
        "algorithm": plan.algorithm,
        "algorithm_version": plan.algorithm_version,
        "axis": axis,
        "first_index": int(first_index),
        "last_index": int(last_index),
        "depth": int(plan.depth),
        "label_id": int(plan.label_id),
        "spacing": list(plan.spacing),
        "overwrite_mode": plan.overwrite_mode,
        "offsets": plan.offsets,
        "voxels_changed": int(plan.voxels_changed),
        "source_version": int(source_version),
        "bbox": list(bbox) if bbox is not None else None,
    }


def _same_request(existing_payload: dict, payload: dict) -> bool:
    """Does a replayed idempotency key describe the same request?

    Compared on the *inputs*, not the outputs: two calls with the same key must
    describe the same interpolation, or the caller has reused a key for a
    different operation and deserves an error rather than someone else's
    result.
    """
    keys = ("algorithm", "algorithm_version", "axis", "first_index",
            "last_index", "depth", "label_id", "spacing", "overwrite_mode")
    return all(existing_payload.get(k) == payload.get(k) for k in keys)


@transaction.atomic
def apply_interpolation(*, task: AnnotationTask, actor, plan: InterpolationPlan,
                        axis: str, first_index: int, last_index: int,
                        write_slice, bbox=None, idempotency_key: str = "",
                        expected_version: int | None = None
                        ) -> AnnotationOperation:
    """Commit an interpolation plan and record exactly one Phase 7 operation.

    ``write_slice(offset, mask)`` is injected by the caller: the service does
    not know how labels are stored, which keeps this testable without a volume
    on disk and keeps the storage format free to change.

    Atomic from the user's perspective. The plan is computed *before* this is
    called, so the transaction covers only the operation row and the writes; a
    failure anywhere rolls back both, and there is no half-applied state.

    One operation, never one per slice or per voxel — §E8's "single undoable
    transaction", and Phase 7's bounded-payload rule.
    """
    _require_enabled()
    if not operations_enabled():
        raise InterpolationError(
            "Applying an interpolation records an annotation operation, which "
            "needs FEATURE_ANNOTATION_OPS.",
            reason="operations_disabled",
        )

    locked = AnnotationTask.objects.select_for_update().filter(pk=task.pk).first()
    if locked is None:
        raise InterpolationError("Task no longer exists.", reason="gone")
    if locked.annotation_locked:
        raise InterpolationError(
            "This task is locked for further annotation.", reason="locked"
        )

    source_version = current_version(locked)
    payload = _payload_for(
        plan, axis=axis, first_index=first_index, last_index=last_index,
        source_version=source_version, bbox=bbox,
    )

    # Idempotency: a replay must return the original *and not re-apply*.
    # Phase 7 returns the original row on a key match, so the parameter
    # comparison has to happen here — otherwise a reused key would hand back a
    # result that does not describe what was asked for.
    if idempotency_key:
        existing = AnnotationOperation.objects.filter(
            task=locked, actor=actor, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            if not _same_request(existing.payload or {}, payload):
                raise InterpolationError(
                    "This idempotency key was already used for a different "
                    "interpolation. Use a new key.",
                    reason="idempotency_conflict",
                )
            return existing

    try:
        operation = append_operation(
            task=locked, actor=actor,
            kind=AnnotationOperation.Kind.PREDICT_COMMIT,
            payload=payload, idempotency_key=idempotency_key,
            expected_version=expected_version,
        )
    except OperationError:
        # Includes VersionConflict. Nothing has been written yet, so the
        # caller's slices are untouched; re-raised unchanged so the conflict
        # keeps its rebase data.
        raise

    # Writes happen last: if one fails the operation row rolls back with it.
    for offset, mask in sorted(plan.masks.items()):
        write_slice(offset, mask)

    return operation
