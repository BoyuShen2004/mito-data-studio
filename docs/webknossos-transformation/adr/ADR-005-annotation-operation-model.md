# ADR-005 — Annotation operation model: append-only op log, materialized state, session-based active time

**Status:** accepted, 2026-07-29
**Phase:** 7 (annotation operation model)
**Depends on:** Phases 0–1 (per phase map), PostgreSQL
**Gate (phase map):** op log design approved
**Related:** ADR-001 … ADR-004

---

## 1. Authoritative scope

| Source | Says |
|---|---|
| `CLAUDE_CODE_MASTER_PROMPT.md` §E7 | "Introduce server-ack **op log** + sparse chunk versions (see `22-target-persistence-and-recovery.md`). Feature-flag beside current PUT label-ids. Migration path from working TIFF memmap. **Flag: `FEATURE_ANNOTATION_OPS`**" |
| `27-claude-code-phase-map.md` row 7 | "Annotation operation model", depends on **0–1**, gate **"op log design approved"** |
| `22-target-persistence-and-recovery.md` | `AnnotationInstance → Snapshot / OperationLog[] (id, prev_id, type, payload, client_ts, server_ts, user) / ChunkDelta[] / SaveCursor`; write path, undo/redo, recovery matrix |
| `16-target-domain-model.md` | `Assignment / TaskInstance → **AnnotationSession (op log, timing)**` |
| `14-complete-feature-gap-matrix.md` row 19 | Time tracking: mito has "timestamps only"; verdict **"Add session timing"** |

### Required

1. **Append-only operation log** with server acknowledgement.
2. **Sparse chunk versions** — optimistic concurrency on what changed.
3. **Beside** the current `PUT label-ids`, behind a flag — not replacing it.
4. **AnnotationSession** carrying both the op log link and timing.
5. **Active-time tracking** — gap matrix row 19, "add session timing".

### Explicitly excluded

- Interpolation → Phase 8 (gate: golden tests).
- Frontend autosave queue, IndexedDB drafts, client rebase → the client half of
  doc 22, not this phase.
- Migrating the working TIFF memmap to zarr → doc 22 names a *path*, and
  changing the storage format is a separate, larger decision.
- Any change to Phase 6 dashboard field semantics (§8 below).
- Snapshot/zarr chunk store — see §5, deliberately deferred.

### Flag

**`FEATURE_ANNOTATION_OPS`**, default False. The master prompt names it
explicitly, so that name is used rather than the more descriptive
`FEATURE_ANNOTATION_OPERATIONS` — an authoritative name beats a nicer one.

## 2. Conflicts found, and how they were resolved

### Conflict A — is active-time tracking in Phase 7 at all?

§E7 says only "op log + sparse chunk versions". It does not mention time. §E6
lists "time tracking" under **Phase 6**, and the Phase 6 report deferred it to
Phase 7 — a forward-looking guess, which ADR-004 §2C established does not
itself create scope.

**Resolution — it belongs here, on written evidence, not on the recap.** Doc 16
places `AnnotationSession (op log, timing)` as a single node: the session that
carries operations is the same object that carries timing. The gap matrix's
verdict for time tracking is "**Add session timing**". Building the session for
the op log and then not recording its duration would leave the roadmap's own
model half-built.

Phase 6 deliberately shipped `mean_elapsed_*` as wall-clock and said so. Phase 7
adds *measured* active time as **separate, differently-named fields**. Phase 6's
numbers are not redefined — §8.

### Conflict B — doc 22's `ChunkDelta` versus "no voxel payloads in PostgreSQL"

Doc 22 lists `ChunkDelta[] (chunk_coord → bytes or RLE, version)` as a stored
entity. Read literally beside the instruction "do not place large segmentation
arrays or voxel payloads directly into PostgreSQL", these pull in opposite
directions: a slice of a 17 MPix volume RLE-encodes to far more than belongs in
a row.

**Resolution.** PostgreSQL stores the operation's **metadata and version
vector** — what changed, by whom, when, at which sequence, over which chunks,
with what digest and byte size. The voxel bytes stay where they already live:
the working memmap under `MITO_DATA_ROOT`. `payload_ref` names the artifact when
one exists.

`payload` is capped at **16 KiB** and validated; anything larger must be a
reference. This keeps the log scannable — a dashboard or a history view reads
metadata without touching voxels — and honours both documents' intent: doc 22
wants versioned deltas, not a blob store in the database.

### Conflict C — what is "the annotation" an operation belongs to?

Doc 22 hangs the op log off `AnnotationInstance`; doc 16 hangs the session off
`Assignment / TaskInstance`. mito's `TaskInstance` (Phase 2) exists but is inert
unless `FEATURE_TASK_HIERARCHY` is on, and the phase map says Phase 7 depends on
**0–1**, *not* on Phase 2.

**Resolution — operations belong to the `AnnotationTask`,** with an optional
`TaskInstance` link. The task is the unit that owns the volume and the working
label, it exists in every configuration, and this keeps Phase 7 free of a
Phase 2 dependency the phase map explicitly does not declare. When the hierarchy
is on, the instance is recorded too, so nothing is lost.

## 3. Operation identity and ordering

| Question | Decision |
|---|---|
| Global identity | **UUIDv4** primary key. Client-generatable, so an op is identifiable before the server sees it. |
| Ordering | **Per task**, dense monotonic `seq` starting at 1. Not global (a single hot counter), not per session (two sessions on one task could not be ordered). |
| Sequence allocation | Under `SELECT … FOR UPDATE` on the **task row** — the same lock ordering Phases 3–5 use, so no new deadlock surface. A `UniqueConstraint(task, seq)` makes a gap or duplicate impossible even if the service is bypassed. |
| Duplicate retries | `idempotency_key`, unique per `(task, actor, key)`. A replay returns the original operation. |
| Out-of-order | `expected_version` (the client's last known `seq`). A mismatch is a **409 conflict** carrying the current version and the operations the client has not seen, so it can rebase rather than reload. |
| Concurrent editors | Optimistic: last writer wins the sequence, loser gets 409 with the delta. Pessimistic locking of a whole task would make two people painting different slices block each other for no reason. |

## 4. Operation content

**Domain-specific commands**, not opaque diffs. Types: `paint_slice`,
`erase_slice`, `track_slices`, `predict_commit`, `merge_labels`,
`split_components`, `watershed`, `undo`, `redo`.

`payload` is a **versioned JSON document** (`schema_version`, integer). Unknown
future versions are rejected on write and *surfaced, not silently skipped*, on
read — a history that quietly drops operations it does not understand is worse
than one that admits it cannot render them.

No pickle, no arbitrary object serialization: payload must be JSON-serializable
primitives, enforced at write.

**Immutable once written.** Enforced in `save()`, the same mechanism and for the
same reason as `ReviewRecord` in Phase 5, with the same documented gap:
`queryset.update()` bypasses it, and a test pins that so nobody assumes
otherwise.

## 5. State materialization

**Materialized current state, with the op log as history — not event sourcing.**

The working memmap TIFF *is* the materialized state and stays authoritative. The
op log records what happened; it is not replayed to answer a read.

This is the single most important decision here, and it is deliberate:

- The instruction is explicit — "do not accept an implementation that requires
  replaying an unbounded operation history on every normal read".
- Reads are the hot path. The viewer fetches slices constantly; replaying even
  100 operations per slice read would be indefensible when a memmap seek is
  already O(1).
- It makes the flag genuinely inert: with `FEATURE_ANNOTATION_OPS` off, reads
  and writes are byte-identical to today, because the log is additive
  bookkeeping rather than the source of truth.

**Consequence, stated plainly:** the log is an *audit and undo* substrate, not a
recovery mechanism, until a snapshot/chunk-delta store exists. Full replay-based
recovery needs the zarr chunk store doc 22 describes, which is **not** in this
phase. `AnnotationTask.op_version` is the materialized cursor
(doc 22's `SaveCursor`).

Corruption detection: each operation stores a `payload_digest`. A history whose
digests do not match its payloads is detectable without replay.

**The version cursor is derived, not stored.** Doc 22 names a `SaveCursor`, and
an earlier draft of this ADR put an `op_version` column on `AnnotationTask`.
That was dropped during implementation: a denormalized counter can drift from
the rows it summarizes, which is the exact failure mode ADR-004 §3 refused for
statistics and Phase 2 spent a CHECK constraint guarding against. `MAX(seq)` over
`idx_operation_recent` is one indexed lookup — the cost does not grow with
history — and it cannot disagree with the log because it *is* the log.

Legacy tasks with no operations are valid — version 0, empty history — and
require no backfill. **No existing annotation state is converted.**

## 6. Undo and redo

- **Undo appends an inverse operation.** Nothing is ever physically deleted.
- **Redo appends another operation**, inverting the undo.
- `inverse_of` links an operation to the one it reverses; `undone_at` marks the
  reversed operation so a history view can grey it without losing it.
- Only the **latest un-undone operation on the task** may be undone, and only by
  its own actor or a manager. Undoing out of the middle of a history would
  require rebasing everything after it, which the materialized-state model
  cannot honour.
- **Blocked after submission or approval** when the task is `annotation_locked`:
  Phase 5 made the lock the single gate on "may this still be edited", and undo
  is an edit.
- Undo of an operation whose inverse cannot be computed (no stored prior state)
  fails **atomically** with a clear reason rather than writing a half-applied
  inverse.

## 7. Active time — precise definition

**Active time is the sum of server-measured intervals between heartbeats from an
open editing session, where each interval is capped and idle gaps are excluded.**

| Aspect | Decision |
|---|---|
| Session start | Explicit `start_session`; returns a session id the client sends with heartbeats. |
| Heartbeat | Client posts periodically (suggested 30 s). Server credits `now − last_heartbeat`. |
| Max credited interval | **120 s** per heartbeat. A tab asleep for an hour then waking credits 120 s, not an hour. |
| Idle timeout | **300 s**. A gap longer than this credits **nothing** and starts a new active span. |
| Client timestamps | **Never trusted for duration.** `client_ts` is stored for diagnostics only; every credited second comes from `timezone.now()`. |
| Browser disconnect / tab crash | No further heartbeats; the session goes stale and is closed by the sweep, crediting nothing after the last heartbeat. |
| Duplicate tabs | Each tab gets its own session. **Overlap is de-duplicated at aggregation** by merging intervals per (user, task) — two tabs open for one hour is one hour, not two. |
| Overlapping sessions | Same mechanism; asserted by test. |
| Negative or zero | Impossible by construction: intervals are `max(0, min(delta, cap))`. |
| Storage | UTC always (`USE_TZ`), so DST and timezone are non-issues. |
| Aggregation | Per user, task, project, and UTC day. |
| Privacy / retention | Sessions record *that* someone worked and for how long, never what they viewed. No IP, no user-agent, no cursor telemetry. Retention is the operator's policy; no automatic purge ships. |

## 8. Boundaries between the existing systems

| System | Records | Volume | Mutable |
|---|---|---|---|
| `AuditEvent` (Phase 1) | permission-relevant *decisions* | low | no |
| `ReviewRecord` (Phase 5) | review verdicts | low | no |
| `AnnotationSubmission` (Phase 5) | submitted rounds | low | superseded, not edited |
| **`AnnotationOperation`** (Phase 7) | **individual edits** | **high** | **no** |
| **`WorkSession`** (Phase 7) | **when someone was working** | medium | append-heavy |
| Dashboards (Phase 6) | aggregates over all of the above | — | — |

**`AuditEvent` is deliberately not used for operations.** It is the
permission-change log; a brush stroke is not a permission change, and pouring a
high-volume edit stream into it would drown the signal it exists to carry. This
is an explicit instruction and it matches Phase 1's design intent.

Phase 6's `mean_elapsed_*` fields are **not** touched. Active time, when
surfaced, gets separate keys (`active_seconds_*`) plus a **coverage** figure
saying what fraction of the work has session data at all — legacy annotations
have none, and fabricating it is forbidden.

## 9. Concurrency and transactions

| Concern | Decision |
|---|---|
| Lock target | The **task row**, only while allocating a sequence. |
| Lock ordering | Task first, always — identical to Phases 3–5. |
| Model | Optimistic (`expected_version`) for callers; pessimistic only for the brief allocation. |
| Isolation | READ COMMITTED (Django default). The unique `(task, seq)` constraint, not the isolation level, is what makes duplicates impossible. |
| Idempotency | Unique `(task, actor, idempotency_key)`; replay returns the original. |
| Retry | Callers retry on 409 after rebasing. The service does not silently retry a conflicting write — that would hide a real divergence. |
| Crash after insert, before response | The client retries with the same idempotency key and receives the original operation. This is exactly the case the key exists for. |
| Transaction | One per append: lock, allocate, insert, bump `op_version`. |

## 10. Rollout

- `FEATURE_ANNOTATION_OPS=False` by default.
- **No dual-write and no backfill.** The materialized state is already
  authoritative, so there is nothing to reconcile — which is the main practical
  advantage of §5 over event sourcing.
- Migration is **additive**: two new tables, three nullable columns on
  `AnnotationTask`. Reversible.
- Rollback: turning the flag off stops recording. Existing operations remain and
  are simply not read. Rolling the *code* back leaves unused tables.
- Correctness before authority: the log becomes authoritative for undo only
  after `test_materialized_state_matches_operation_history` passes on real data
  — and it never becomes authoritative for *reads* in this phase at all.

## 11. Acceptance criteria

1. Flag off → reads and writes byte-identical to Phase 6.
2. Sequence is dense, monotonic, gap-free under concurrent writers.
3. Replaying an idempotency key never creates a second operation.
4. A stale `expected_version` yields 409 with actionable rebase data.
5. Undo appends; nothing is ever deleted.
6. Active time is never negative, never double-counted across overlapping
   sessions, and never derived from client clocks.
7. Reading history is bounded and does not scale with total history.
8. Appending is constant-cost regardless of how many operations precede it.
