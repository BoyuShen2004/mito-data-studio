"""Phase 7 — append-only annotation operation log.

Records *what changed*, in order, with server acknowledgement. It does **not**
hold the annotation state: the working memmap TIFF remains materialized and
authoritative for every read. See ADR-005 §5 — replaying history on a normal
read would be indefensible when a memmap seek is already O(1), and it is what
lets this whole phase be genuinely inert behind its flag.

Ordering is per task, dense and monotonic. The sequence is allocated under a
lock on the **task row** — the same lock target and ordering Phases 3-5 use, so
no new deadlock surface — and a unique `(task, seq)` constraint makes a gap or
duplicate impossible even if this module is bypassed.
"""

from __future__ import annotations

import hashlib
import json
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import AnnotationOperation, AnnotationTask

logger = logging.getLogger(__name__)

# Payload schema version. Bump when the meaning of a payload field changes;
# readers must refuse versions they do not understand rather than guess.
CURRENT_SCHEMA_VERSION = 1

# How many operations a history read returns by default. Bounded so a task with
# a long history cannot turn one request into an unbounded scan.
DEFAULT_HISTORY_LIMIT = 100
MAX_HISTORY_LIMIT = 1000


class OperationError(Exception):
    """An operation that cannot be recorded."""

    def __init__(self, message: str, *, reason: str = "invalid"):
        super().__init__(message)
        self.reason = reason


class VersionConflict(OperationError):
    """The caller's expected version is stale.

    Carries what the client needs to rebase — the current version and the
    operations it has not seen — rather than merely saying "no". A conflict the
    client cannot act on forces a full reload, which is what the op log exists
    to avoid.
    """

    def __init__(self, current_version: int, missed):
        super().__init__(
            f"Stale version: task is at {current_version}.", reason="conflict"
        )
        self.current_version = current_version
        self.missed = missed


def operations_enabled() -> bool:
    """Phase 7 records nothing unless this is on."""
    return bool(getattr(settings, "FEATURE_ANNOTATION_OPS", False))


def _require_enabled() -> None:
    if not operations_enabled():
        raise OperationError(
            "Annotation operations are disabled (FEATURE_ANNOTATION_OPS).",
            reason="disabled",
        )


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------


def _canonical(payload: dict) -> str:
    """Stable JSON for digesting. Sorted keys so the digest is reproducible.

    Deliberately **strict** — no ``default=`` fallback. A coercion hook would
    make `str()` of any object "serialisable", so a set or a model instance
    would pass validation here and then fail at the jsonb insert, turning a
    clear rejection into a 500. Strictness is what makes the payload contract
    real.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def validate_payload(payload, *, schema_version: int) -> tuple[dict, str]:
    """Check a payload is small, JSON-safe and of a known schema.

    Returns ``(payload, digest)``.

    Three refusals, each deliberate:

    * an unknown ``schema_version`` — a reader that guesses at a format it does
      not know produces confident nonsense;
    * anything not JSON-serialisable — no pickle, no arbitrary objects, because
      an operation log is a durable format and must stay readable by tools that
      are not this Python process;
    * anything over ``MITO_OP_PAYLOAD_MAX_BYTES`` — voxels belong in the
      working memmap, referenced by ``payload_ref``, not inlined here.
    """
    if schema_version != CURRENT_SCHEMA_VERSION:
        raise OperationError(
            f"Unsupported operation schema version {schema_version}; "
            f"this server writes and reads version {CURRENT_SCHEMA_VERSION}.",
            reason="unsupported_schema",
        )
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise OperationError("Operation payload must be a JSON object.")

    try:
        blob = _canonical(payload)
    except (TypeError, ValueError) as exc:
        raise OperationError(f"Operation payload is not JSON-serialisable: {exc}")

    limit = int(getattr(settings, "MITO_OP_PAYLOAD_MAX_BYTES", 16 * 1024))
    size = len(blob.encode("utf-8"))
    if size > limit:
        raise OperationError(
            f"Operation payload is {size} bytes, over the {limit}-byte limit. "
            f"Store voxel data in the working label and reference it with "
            f"payload_ref instead.",
            reason="payload_too_large",
        )
    return payload, hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def current_version(task: AnnotationTask) -> int:
    """The task's latest operation sequence, or 0 for a legacy task.

    One indexed lookup (``idx_operation_recent``), not a COUNT — the cost must
    not grow with history length.
    """
    row = (
        AnnotationOperation.objects.filter(task=task)
        .order_by("-seq")
        .values_list("seq", flat=True)
        .first()
    )
    return int(row or 0)


def history(task: AnnotationTask, *, limit: int = DEFAULT_HISTORY_LIMIT,
            after_seq: int | None = None):
    """Operations for ``task``, oldest first, bounded.

    ``after_seq`` is the rebase path: give me everything I have not seen.
    """
    limit = max(1, min(int(limit), MAX_HISTORY_LIMIT))
    qs = AnnotationOperation.objects.filter(task=task).select_related("actor")
    if after_seq is not None:
        qs = qs.filter(seq__gt=int(after_seq))
    return list(qs.order_by("seq")[:limit])


def _existing_for_key(task, actor, key: str):
    if not key:
        return None
    return AnnotationOperation.objects.filter(
        task=task, actor=actor, idempotency_key=key
    ).first()


# ---------------------------------------------------------------------------
# Appending
# ---------------------------------------------------------------------------


@transaction.atomic
def append_operation(
    *,
    task: AnnotationTask,
    actor,
    kind: str,
    payload: dict | None = None,
    payload_ref: str = "",
    schema_version: int = CURRENT_SCHEMA_VERSION,
    session=None,
    client_ts=None,
    idempotency_key: str = "",
    expected_version: int | None = None,
) -> AnnotationOperation:
    """Record one edit and return it.

    Constant cost regardless of how many operations precede it: one locked read
    of the task row, one max(seq) lookup, one insert.

    ``expected_version`` is optimistic concurrency. Omit it and the operation
    appends unconditionally; supply the sequence you last saw and a competing
    writer will raise :class:`VersionConflict` carrying what you missed.
    """
    _require_enabled()

    if kind not in AnnotationOperation.Kind.values:
        raise OperationError(f"Unknown operation kind {kind!r}.")

    # Idempotency is checked before any work: a replay must be cheap.
    replay = _existing_for_key(task, actor, idempotency_key)
    if replay is not None:
        return replay

    payload, digest = validate_payload(payload, schema_version=schema_version)

    # Lock the task row. Everything below decides the sequence, and two
    # concurrent appends must not each decide it from their own snapshot.
    locked = AnnotationTask.objects.select_for_update().filter(pk=task.pk).first()
    if locked is None:
        raise OperationError("Task no longer exists.", reason="gone")

    version = current_version(locked)
    if expected_version is not None and int(expected_version) != version:
        raise VersionConflict(version, history(locked, after_seq=expected_version))

    try:
        # Nested atomic == savepoint. Without it an IntegrityError poisons the
        # whole transaction and the recovery lookup below cannot run:
        # "You can't execute queries until the end of the 'atomic' block".
        # The savepoint confines the rollback to the failed insert, which is
        # exactly the concurrent-replay case this except branch exists to
        # resolve.
        with transaction.atomic():
            op = AnnotationOperation.objects.create(
                task=locked,
                session=session,
                actor=actor,
                seq=version + 1,
                kind=kind,
                schema_version=schema_version,
                payload=payload,
                payload_ref=payload_ref or "",
                payload_digest=digest,
                client_ts=client_ts,
                idempotency_key=idempotency_key or "",
            )
    except IntegrityError:
        # Either two appends raced for the same sequence, or the same
        # idempotency key was replayed concurrently. Both are benign and both
        # resolve by reading what the winner wrote — the row lock above makes
        # the first rare, and the unique index is what adjudicates either way.
        replay = _existing_for_key(task, actor, idempotency_key)
        if replay is not None:
            return replay
        raise OperationError(
            "Concurrent append conflicted; retry with the current version.",
            reason="conflict",
        )
    return op


# ---------------------------------------------------------------------------
# Undo / redo
# ---------------------------------------------------------------------------


def _mark_undone(op: AnnotationOperation, when) -> None:
    """Stamp ``undone_at`` without going through the immutability guard.

    ``AnnotationOperation.save()`` refuses every post-insert write, which is the
    point. This is the one narrow, explicit exception — a queryset update, so
    the guard is bypassed deliberately and visibly rather than by weakening it.
    The row's content is untouched; only the "this was reversed" marker moves.
    """
    AnnotationOperation.objects.filter(pk=op.pk).update(undone_at=when)
    op.undone_at = when


def latest_undoable(task: AnnotationTask):
    """The most recent operation that may still be undone, or ``None``.

    Undo/redo bookkeeping operations are themselves skipped: undoing an undo is
    redo, which is a separate call.
    """
    return (
        AnnotationOperation.objects.filter(task=task, undone_at__isnull=True)
        .exclude(kind__in=(AnnotationOperation.Kind.UNDO,
                           AnnotationOperation.Kind.REDO))
        .order_by("-seq")
        .first()
    )


def can_undo(user, task: AnnotationTask, op: AnnotationOperation) -> bool:
    """Only the operation's own author, or a manager, may reverse it."""
    from accounts.roles import is_manager

    if op.actor_id and op.actor_id == getattr(user, "id", None):
        return True
    return is_manager(user)


@transaction.atomic
def undo(task: AnnotationTask, *, actor, idempotency_key: str = "")\
        -> AnnotationOperation:
    """Reverse the latest operation by **appending** its inverse.

    Nothing is deleted. The reversed operation stays in the log with
    ``undone_at`` set, so a history view can grey it out rather than lose it.

    Only the latest un-undone operation is eligible: undoing from the middle
    would require rebasing everything after it, which the materialized-state
    model cannot honour (ADR-005 §6).

    Refused while the task is ``annotation_locked`` — Phase 5 made that the
    single gate on "may this still be edited", and an undo is an edit.
    """
    _require_enabled()

    locked_task = AnnotationTask.objects.select_for_update().filter(pk=task.pk).first()
    if locked_task is None:
        raise OperationError("Task no longer exists.", reason="gone")
    if locked_task.annotation_locked:
        raise OperationError(
            "This task is locked for further annotation; undo is not available.",
            reason="locked",
        )

    target = latest_undoable(locked_task)
    if target is None:
        raise OperationError("There is nothing to undo.", reason="nothing_to_undo")
    if not can_undo(actor, locked_task, target):
        raise OperationError(
            "You may only undo your own operations.", reason="forbidden"
        )

    inverse = append_operation(
        task=locked_task,
        actor=actor,
        kind=AnnotationOperation.Kind.UNDO,
        payload={"undoes_seq": target.seq, "undoes_kind": target.kind},
        session=target.session,
        idempotency_key=idempotency_key,
    )
    # If this raises, the whole transaction rolls back and neither the marker
    # nor the inverse survives — a half-applied undo is worse than none.
    AnnotationOperation.objects.filter(pk=inverse.pk).update(inverse_of=target)
    inverse.inverse_of = target
    _mark_undone(target, timezone.now())
    return inverse


@transaction.atomic
def redo(task: AnnotationTask, *, actor, idempotency_key: str = "")\
        -> AnnotationOperation:
    """Reverse the latest undo, by appending again. Also never deletes."""
    _require_enabled()

    locked_task = AnnotationTask.objects.select_for_update().filter(pk=task.pk).first()
    if locked_task is None:
        raise OperationError("Task no longer exists.", reason="gone")
    if locked_task.annotation_locked:
        raise OperationError(
            "This task is locked for further annotation; redo is not available.",
            reason="locked",
        )

    last_undo = (
        AnnotationOperation.objects.filter(
            task=locked_task, kind=AnnotationOperation.Kind.UNDO,
            undone_at__isnull=True,
        )
        .order_by("-seq")
        .first()
    )
    if last_undo is None or last_undo.inverse_of_id is None:
        raise OperationError("There is nothing to redo.", reason="nothing_to_redo")
    if not can_undo(actor, locked_task, last_undo):
        raise OperationError(
            "You may only redo your own operations.", reason="forbidden"
        )

    restored = last_undo.inverse_of
    op = append_operation(
        task=locked_task,
        actor=actor,
        kind=AnnotationOperation.Kind.REDO,
        payload={"redoes_seq": restored.seq, "redoes_kind": restored.kind},
        session=last_undo.session,
        idempotency_key=idempotency_key,
    )
    _mark_undone(last_undo, timezone.now())
    # The originally-undone operation is live again.
    AnnotationOperation.objects.filter(pk=restored.pk).update(undone_at=None)
    return op


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------


def verify_history(task: AnnotationTask) -> dict:
    """Check the log is dense and its digests match. No replay involved.

    Corruption is detectable without reconstructing anything, which is the
    other half of choosing materialized state over event sourcing.
    """
    ops = list(
        AnnotationOperation.objects.filter(task=task)
        .order_by("seq")
        .only("id", "seq", "payload", "payload_digest", "schema_version")
    )
    gaps, bad_digests, unknown_schema = [], [], []
    for i, op in enumerate(ops, start=1):
        if op.seq != i:
            gaps.append({"expected": i, "found": op.seq})
        if op.payload_digest:
            actual = hashlib.sha256(_canonical(op.payload).encode("utf-8")).hexdigest()
            if actual != op.payload_digest:
                bad_digests.append(str(op.id))
        if op.schema_version != CURRENT_SCHEMA_VERSION:
            unknown_schema.append(str(op.id))
    return {
        "operations": len(ops),
        "dense": not gaps,
        "gaps": gaps,
        "digest_mismatches": bad_digests,
        "unreadable_schema_versions": unknown_schema,
        "ok": not gaps and not bad_digests,
    }
