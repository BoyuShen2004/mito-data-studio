# Phase 2 — WEBKNOSSOS-style task hierarchy

**Status:** complete, shipped inert behind `FEATURE_TASK_HIERARCHY=False`
**Date:** 2026-07-27
**Depends on:** Phase 1, PostgreSQL
**Gate:** schema approval before the flag is enabled anywhere real

---

## Objective

Introduce `TaskType → Project → Task → TaskInstance` so a task can be a unit of
*work to be done N times*, rather than a row with one assignee — the
precondition for the Phase 3 claim engine.

## Problem

`AnnotationTask` conflated three concepts: the definition of the work, the
single instance of it, and its assignment (`assigned_to`). That makes redundant
annotation impossible, gives no place for reusable instructions or per-type
tool policy, and leaves nothing for a claim engine to decrement.

## Key design decision — `AnnotationTask` *is* the Task

The obvious reading of the master prompt is a fresh `Task` table with existing
rows copied across. **That was rejected.** `AnnotationTask` already carries the
project, volume, bounding box, priority, difficulty, deadline and instructions
that the hierarchy's Task needs, and — decisively — it is the FK target of
`AnnotationSubmission`, `ReviewRecord`, and `HardCase`. A parallel table would
have meant repointing all of them, a genuinely destructive migration, to gain
nothing.

So `AnnotationTask` gained the missing fields, and the one genuinely new
concept — *one annotator's copy of the work* — became `TaskInstance`.

| Hierarchy concept | Where it lives |
|---|---|
| Task Type | **new** `annotation.TaskType` |
| Project (priority, paused) | `projects.Project` + 2 fields |
| Task | **existing** `annotation.AnnotationTask` + 3 fields |
| Task Instance / Assignment | **new** `annotation.TaskInstance` |

## What shipped

| Area | Detail |
|---|---|
| Models | `TaskType`, `TaskInstance`; `AnnotationTask.{task_type_ref, total_instances, pending_instances}`; `Project.{priority, paused}` |
| Services | `annotation/instances.py` — claim, release, retotal, recompute, claim queue |
| Choices | `InstanceState`, `COUNTING_INSTANCE_STATES` |
| Flag | `FEATURE_TASK_HIERARCHY`, default **False** |
| Migrations | `annotation.0007` (schema), `annotation.0008` (backfill), `projects.0007` |
| Tests | `annotation/test_task_hierarchy.py` — 32 tests |

### The invariant

```
pending_instances == total_instances - (instances occupying a slot)
```

"Occupying a slot" = `claimed | in_progress | submitted | completed`.
Cancelled work releases its slot, mirroring WEBKNOSSOS's
`countsAsTaskInstance`.

The counter is moved incrementally by the service layer (so claiming is one
indexed read, not a `COUNT`), and `recompute_pending()` reconciles it. A test
asserts the incremental and recomputed values agree, and another asserts
recompute repairs a deliberately corrupted counter.

### Only a lower bound in the database

```python
CheckConstraint(condition=Q(pending_instances__gte=0),
                name="pending_instances_non_negative")
```

**This is the audit correction made concrete.** The research pack (doc `04`,
E12) claimed WEBKNOSSOS enforces `0 <= pending <= total`. It does not: evolution
`008` added the upper bound and `026` deliberately dropped it, so
`totalInstances` could be reduced below the already-claimed count. Copying the
two-sided version would have made shrinking a partly-claimed task fail at the
database. `test_lowering_below_claimed_work_is_allowed` pins the behaviour.

### Claim queue ordering

`claimable_tasks()` orders by project priority, then task priority, then
`created_at`, then `id`. Upstream orders by **project priority alone** with
`LIMIT 1`, leaving ties to database order — its own docs claim a random
tie-break that the SQL does not implement (`AUDIT_DELTA` §2.4). mito's ordering
is fully deterministic and tested to be stable across repeated calls.

The queue already excludes paused projects, full tasks, and work the user
already holds. Phase 3 adds team/experience/capacity eligibility and wraps it in
the atomic claim.

## Concurrency

`claim_instance()` takes `select_for_update()` on the task row before reading
`pending_instances`, so two concurrent claimants serialise rather than both
seeing a free slot. **This is only a real lock on PostgreSQL** — the Phase 0
baseline measured `select_for_update()` as a silent no-op on SQLite, which is
why the database migration preceded this phase.

## Migrations

| Migration | Operation | Reversible |
|---|---|---|
| `annotation.0007` | Add 3 fields, create 2 models, 3 constraints | yes |
| `annotation.0008` | Backfill blueprints + instances from legacy rows | yes (`unbackfill`) |
| `projects.0007` | Add `priority`, `paused` | yes |

The backfill maps each task to `total_instances = 1`, derives a `TaskType` per
distinct enum value per organisation, and materialises a `TaskInstance` for any
`assigned_to`, with state derived from the task's status (`rejected` and
`revision_requested` map to `in_progress` — the work is back with the
annotator). Idempotent, and its reverse preserves hand-made blueprints.

## Compatibility

`assigned_to`, `status`, and `task_type` are **untouched**. Every existing
query, serializer, admin screen, and UI path keeps working unchanged, and
`test_legacy_fields_are_untouched_by_phase_2` asserts that claiming an instance
does not disturb them. The two models coexist until Phase 3 switches the read
path behind the flag.

## Not in this phase

- No claim API endpoint (`POST /api/tasks/claim-next/`) — Phase 3
- Eligibility (team, experience, capacity) is not yet applied to the queue
- No admin/UI for task types or multi-instance tasks — Phase 6
- `status` is not yet derived from instance states; they are independent
