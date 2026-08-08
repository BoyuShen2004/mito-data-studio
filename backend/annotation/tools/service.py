"""Phase 9 — annotation-tool orchestration.

One shared boundary for every P1 tool rather than a set of unrelated one-off
paths: validate, plan, apply, record. The cores stay pure; this module is the
only part that knows about Django, the label volume, or the operation log.

The contract is deliberately identical to Phase 8's interpolation service —
plan mutates nothing, apply commits in one transaction and records exactly one
Phase 7 operation — so a caller that has integrated one tool has integrated all
of them.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction

from annotation.models import AnnotationOperation, AnnotationTask
from annotation.operations import (
    OperationError,
    append_operation,
    current_version,
    operations_enabled,
)

from .common import (
    DEFAULT_MAX_FILL_DEPTH,
    DEFAULT_MAX_VOXELS,
    BoundingBox,
    ToolError,
    ToolPlan,
)
from . import flood_fill

logger = logging.getLogger(__name__)


def tools_enabled() -> bool:
    return bool(getattr(settings, "FEATURE_ANNOTATION_TOOLS", False))


def _require_enabled() -> None:
    if not tools_enabled():
        raise ToolError(
            "Annotation tools are disabled (FEATURE_ANNOTATION_TOOLS).",
            reason="disabled",
        )


def _max_voxels() -> int:
    return int(getattr(settings, "MITO_TOOL_MAX_VOXELS", DEFAULT_MAX_VOXELS))


def _max_fill_depth() -> int:
    return int(getattr(settings, "MITO_TOOL_MAX_FILL_DEPTH",
                       DEFAULT_MAX_FILL_DEPTH))


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def plan_flood_fill(*, block, seed, label_id, bbox, overwrite_mode=None
                    ) -> ToolPlan:
    """Compute a fill. Writes nothing, touches no row."""
    _require_enabled()
    from .overwrite import DEFAULT_OVERWRITE_MODE

    return flood_fill.plan(
        block, seed=tuple(seed), label_id=label_id,
        bbox=bbox if isinstance(bbox, BoundingBox)
        else BoundingBox.from_sequence(bbox),
        overwrite_mode=overwrite_mode or DEFAULT_OVERWRITE_MODE,
        max_voxels=_max_voxels(), max_depth=_max_fill_depth(),
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _payload_for(plan: ToolPlan, *, source_version: int) -> dict:
    """Metadata only — never voxels, per ADR-005 §2 and ADR-007 §8."""
    return {
        "tool": plan.tool,
        "tool_schema_version": plan.tool_schema_version,
        "label_id": int(plan.label_id),
        "overwrite_mode": plan.overwrite_mode,
        "bbox": plan.bbox.as_list(),
        "voxels_changed": int(plan.voxels_changed),
        "source_version": int(source_version),
        "params": plan.params,
        "warnings": plan.warnings[:8],
    }


def _same_request(existing: dict, payload: dict) -> bool:
    """Compared on inputs, not results.

    Phase 7 hands back the original row on a key match, so a caller reusing a
    key for a *different* request would otherwise receive a result that does not
    describe what they asked for.
    """
    keys = ("tool", "tool_schema_version", "label_id", "overwrite_mode",
            "bbox", "params")
    return all(existing.get(k) == payload.get(k) for k in keys)


@transaction.atomic
def apply_tool(*, task: AnnotationTask, actor, plan: ToolPlan, write_slice,
               idempotency_key: str = "", expected_version: int | None = None
               ) -> AnnotationOperation:
    """Commit a tool plan and record exactly one Phase 7 operation.

    ``write_slice(offset, mask)`` is injected, so this service never learns how
    labels are stored — the same seam Phase 8 uses, which is what makes both
    testable without a volume on disk.

    The plan is computed before this is called, so the transaction spans only
    the operation row and the writes: a failure anywhere rolls back both and
    leaves no partial state.
    """
    _require_enabled()
    if not operations_enabled():
        raise ToolError(
            "Applying a tool records an annotation operation, which needs "
            "FEATURE_ANNOTATION_OPS.",
            reason="operations_disabled",
        )

    locked = AnnotationTask.objects.select_for_update().filter(pk=task.pk).first()
    if locked is None:
        raise ToolError("Task no longer exists.", reason="gone")
    if locked.annotation_locked:
        raise ToolError("This task is locked for further annotation.",
                        reason="locked")

    source_version = current_version(locked)
    payload = _payload_for(plan, source_version=source_version)

    if idempotency_key:
        existing = AnnotationOperation.objects.filter(
            task=locked, actor=actor, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            if not _same_request(existing.payload or {}, payload):
                raise ToolError(
                    "This idempotency key was already used for a different "
                    "tool request. Use a new key.",
                    reason="idempotency_conflict",
                )
            return existing

    try:
        operation = append_operation(
            task=locked, actor=actor,
            kind=AnnotationOperation.Kind.PAINT_SLICE,
            payload=payload, idempotency_key=idempotency_key,
            expected_version=expected_version,
        )
    except OperationError:
        # Includes VersionConflict. Nothing written yet, so the caller's labels
        # are untouched and the conflict keeps its rebase data.
        raise

    for offset, mask in sorted(plan.masks.items()):
        if mask.any():
            write_slice(offset, mask)

    return operation
