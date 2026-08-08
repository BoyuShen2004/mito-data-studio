"""Append-only audit log (Phase 1).

One entry point, :func:`record_audit`, so every permission-relevant action is
written the same way. Deliberately best-effort: an audit failure must never
take down the operation being audited — a lost log line is bad, a 500 on a
successful permission grant is worse. Failures are logged, not raised.

Target is captured as ``(type, id)`` rather than a FK so an event outlives the
object it describes; deleting a team must not erase the record of who was on it.
"""

from __future__ import annotations

import logging

from .models import AuditEvent

logger = logging.getLogger(__name__)


def record_audit(actor, verb, target=None, **metadata) -> AuditEvent | None:
    """Write one audit row. Returns the row, or ``None`` if writing failed."""
    target_type = ""
    target_id = ""
    if target is not None:
        target_type = target.__class__.__name__
        target_id = str(getattr(target, "pk", "") or "")

    # An unauthenticated actor is recorded as `None` (system), not as an error.
    actor_obj = actor if getattr(actor, "is_authenticated", False) else None

    try:
        return AuditEvent.objects.create(
            actor=actor_obj,
            verb=str(verb),
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {},
        )
    except Exception:  # noqa: BLE001 - never let auditing break the action
        logger.exception(
            "Failed to record audit event %s on %s#%s", verb, target_type, target_id
        )
        return None


def record_audit_bulk(entries) -> int:
    """Write many audit rows in one statement. Returns how many were written.

    ``entries`` is an iterable of ``(actor, verb, target, metadata_dict)``.

    Exists because the Phase 4 scheduler audits a whole batch at once: calling
    :func:`record_audit` per assignment made the audit write the *only*
    per-item cost in an otherwise constant-cost tick — 40 INSERTs for 40
    assignments, which is precisely the shape ADR-002 set out to avoid.

    Best-effort in the same way and for the same reason as `record_audit`.
    """
    rows = []
    for actor, verb, target, metadata in entries:
        target_type = ""
        target_id = ""
        if target is not None:
            target_type = target.__class__.__name__
            target_id = str(getattr(target, "pk", "") or "")
        rows.append(AuditEvent(
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            verb=str(verb),
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {},
        ))
    if not rows:
        return 0
    try:
        AuditEvent.objects.bulk_create(rows)
        return len(rows)
    except Exception:  # noqa: BLE001 - never let auditing break the action
        logger.exception("Failed to record %d audit events in bulk", len(rows))
        return 0


def audit_trail(target, *, limit: int = 100):
    """Recent events for one object, newest first."""
    return AuditEvent.objects.filter(
        target_type=target.__class__.__name__,
        target_id=str(getattr(target, "pk", "") or ""),
    )[:limit]
