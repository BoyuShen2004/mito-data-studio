# Phase 7 — Annotation operation model

**Status:** complete, shipped inert behind `FEATURE_ANNOTATION_OPS`
**Date:** 2026-07-29
**Depends on:** Phases 0–1 (per phase map)
**Gate (phase map):** op log design approved
**Design record:** [ADR-005](../adr/ADR-005-annotation-operation-model.md)

---

## Objective

Master prompt §E7: *"Introduce server-ack **op log** + sparse chunk versions.
Feature-flag beside current PUT label-ids. Migration path from working TIFF
memmap. Flag: `FEATURE_ANNOTATION_OPS`."*

The flag name is the master prompt's, not the more descriptive
`FEATURE_ANNOTATION_OPERATIONS` — an authoritative name beats a nicer one.

## What shipped

| Area | Detail |
|---|---|
| Models | `AnnotationOperation` (append-only op log), `WorkSession` (op log owner + timing, per doc 16) |
| Services | `annotation/operations.py`, `annotation/sessions.py` |
| Migration | `annotation.0012_annotation_operations` — **two new tables, additive only** |
| Flag | `FEATURE_ANNOTATION_OPS`, default **False** |
| Tests | `annotation/test_operations.py` — **86 tests**; smoke matrix → 58 tests / 9 configs |
| Benchmark | `benchmarks/bench_operations.py` |

**No HTTP endpoint and no frontend.** §E7 asks for the model "beside the current
PUT label-ids"; the client half of doc 22 (autosave queue, IndexedDB drafts,
rebase) is not in this phase.

## Conflicts resolved

**A — is active time even in Phase 7?** §E7 says only "op log + sparse chunk
versions". §E6 lists "time tracking" under Phase 6, and the Phase 6 report
deferred it here — a forward-looking guess, which ADR-004 established does not
create scope. Resolved on written evidence instead: doc 16 models
`AnnotationSession (op log, timing)` as one node, and the gap matrix's verdict
for time tracking is "**Add session timing**". Building the session for the op
log and not recording its duration would leave the roadmap's own model
half-built.

**B — doc 22's `ChunkDelta` vs "no voxel payloads in PostgreSQL".** The database
stores operation *metadata and version vectors*; voxels stay in the working
memmap, referenced by `payload_ref`. `payload` is capped at 16 KiB and
validated. This keeps the log scannable — a history view reads metadata without
touching image data.

**C — what does an operation belong to?** Doc 22 says `AnnotationInstance`, doc
16 says `TaskInstance`. But the phase map puts Phase 7 on phases **0–1**, and
`TaskInstance` is inert without Phase 2's flag. Operations therefore hang off
`AnnotationTask`, with an optional instance link recorded when the hierarchy is
on. Smoke configuration **I** proves the independence.

## The central decision, and its measurement

ADR-005 §5 chose **materialized current state with the log as history**, not
event sourcing. The instruction was explicit — do not accept an implementation
that replays unbounded history on every read — so the claim was measured rather
than asserted. The same question, "what is the current state", answered three
ways:

| Operations | replay-all | checkpointed (every 100) | **materialized** |
|---|---|---|---|
| 10 | 2.28 ms | 2.27 ms | **0.82 ms** |
| 100 | 7.79 ms | 6.17 ms | **1.27 ms** |
| 1 000 | 30.83 ms | 7.34 ms | **1.81 ms** |
| 10 000 | **275.57 ms** | 8.15 ms | **1.75 ms** |

Replay-all grows linearly — 120× worse from 10 to 10 000 operations.
Checkpointing rescues most of it (276 ms → 8 ms), which is why doc 22 proposes
snapshots. Materialized state is flat at ~1.8 ms and still 4.7× faster than the
checkpointed replay.

**Consequence, stated plainly:** the log is an *audit and undo* substrate, not
yet a recovery mechanism. Replay-based recovery needs the zarr chunk store doc
22 describes, which is **not** in this phase.

### Append and storage

| Existing ops | SQL | p50 | DB |
|---|---|---|---|
| 10 | 7 | 7.02 ms | 4 ms |
| 10 000 | 7 | 8.12 ms | 4 ms |

Append is **constant** in history depth — 7 queries at every size. Storage
settles at ~552 bytes per operation at 10 000 rows (5.5 MB), roughly half of it
index. History reads are one query, 2 ms → 22 ms for `limit=100`.

Sessions: heartbeat 5 queries / 4.5 ms; aggregating 200 sessions 1 query /
10.5 ms.

## Undo and redo — exact semantics

- **Undo appends an inverse operation.** Nothing is ever deleted. The reversed
  operation stays with `undone_at` set so a history view can grey it out.
- **Redo appends again** and clears `undone_at` on the original.
- **Only the latest un-undone operation** may be undone. Undoing from the middle
  would require rebasing everything after it, which materialized state cannot
  honour.
- **Only your own** — or a manager's override.
- **Blocked while `annotation_locked`.** Phase 5 made that the single gate on
  "may this still be edited", and an undo is an edit. So undo is unavailable
  after an approval that closed the task, and available again if a manager
  reopens it.
- Failure rolls back atomically: a half-applied inverse is worse than none.

## Active time — exact definition

> The sum of **server-measured** intervals between heartbeats from an open
> session, each capped at `MITO_SESSION_MAX_HEARTBEAT_SECONDS` (120 s), where
> any gap longer than `MITO_SESSION_IDLE_TIMEOUT_SECONDS` (300 s) credits
> nothing.

| Case | Behaviour |
|---|---|
| Client clock wrong or hostile | Ignored. `client_ts` is diagnostic; every credited second comes from `timezone.now()`. |
| Tab asleep an hour, then wakes | Credits 120 s, not an hour. |
| Gap beyond idle timeout | Credits 0; a new active span begins. |
| Browser crash / closed laptop | No further heartbeats; the sweep closes the session at its **last heartbeat**, so the quiet period is not work. |
| Two tabs | Two sessions. Aggregation merges overlapping intervals per (user, task) — one hour with two tabs is **one** hour. |
| Duplicate/backwards heartbeat | Credits 0. Never negative, by construction and by CHECK constraint. |
| Timezones / DST | Non-issues; everything is UTC. |

De-duplication necessarily merges session *spans* rather than capped seconds, so
an actor who idles inside an overlapping pair can report slightly more than the
capped sum. That is the correct trade: the alternative double-counts.

**Legacy work has no active time and none is fabricated.** `task_active_time`
returns `measured: false`; `project_active_time` returns a `coverage` fraction.

## Boundaries kept

| System | Records | Volume |
|---|---|---|
| `AuditEvent` (Phase 1) | permission decisions | low |
| `ReviewRecord` (Phase 5) | verdicts | low |
| `AnnotationSubmission` (Phase 5) | submitted rounds | low |
| **`AnnotationOperation`** | **individual edits** | **high** |
| **`WorkSession`** | when someone worked | medium |

`AuditEvent` is deliberately **not** used for operations — a brush stroke is not
a permission change, and a high-volume edit stream would drown the signal
Phase 1 built. `test_operations_do_not_appear_in_the_audit_log` pins it.

Asserted by test, not merely intended: **claiming creates no operations**
(assignment is not an edit), and **submission/review transitions create none**
either.

**Phase 6's `mean_elapsed_*` fields are untouched.**
`test_phase6_elapsed_fields_are_unchanged` asserts the dashboard payload gained
no active-time key. Wiring active time into dashboards is a deliberate
non-goal here: doing it silently would redefine a published field.

## Bugs found and fixed

Both caught by tests, not review:

1. **`TransactionManagementError` on concurrent idempotent replay.** The
   `IntegrityError` handler ran queries inside the poisoned transaction. Fixed
   with a nested `atomic()` savepoint confining the rollback to the failed
   insert — the classic Django pitfall, and one that only appears under real
   concurrency.
2. **Payload validation accepted non-JSON values.** `json.dumps(default=str)`
   coerced a `set` into a string, so validation passed and the jsonb insert
   failed — turning a clear rejection into a 500. The canonicaliser is now
   strict.

## Compatibility

With `FEATURE_ANNOTATION_OPS` off: nothing is recorded, every service refuses
with `reason="disabled"`, and every read/write path is byte-identical to
Phase 6. Legacy tasks report version 0 and empty history — valid, not broken,
and **no backfill exists or is needed** because the materialized state was
already authoritative.

Migration is two new tables. Rolling the flag off stops recording; rolling the
code back leaves unused tables.

## Known limits

- `queryset.update()` bypasses operation immutability, exactly as for
  `ReviewRecord`. Pinned by test so nobody assumes otherwise. `undone_at` uses
  that path deliberately and visibly.
- No snapshot/checkpoint store ships. The benchmark *models* checkpointing to
  quantify it; nothing writes checkpoints, because reads do not replay.
- No HTTP API, so nothing calls these services in production yet.
- Storage grows unboundedly with edits; there is no retention or compaction job.

## Not in this phase

- Interpolation → Phase 8.
- Frontend autosave, IndexedDB drafts, client rebase.
- zarr chunk store / memmap format migration.
- Wiring operations into the live `PUT label-ids` path.
- Any change to Phase 6 dashboard semantics.
