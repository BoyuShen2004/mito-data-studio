# ADR-008 — Phase 10: autosave, undo and recovery

**Status:** accepted (design); implementation not started
**Phase:** 10 (autosave / undo / recovery)
**Depends on:** Phase 7 (per phase map)
**Gate (phase map):** **soak refresh tests**
**Scope note:** [PHASE-10-scope-note.md](../phases/PHASE-10-scope-note.md)
**Related:** ADR-005 (operation model), ADR-006, ADR-007

---

## 1. The six kinds of state, named apart

Most of the difficulty in this phase is that "unsaved" means six different
things. Conflating any two of them produces either false data loss or false
confidence, so they are named separately and never merged.

| # | State | Lives in | Durable? | Authoritative for |
|---|---|---|---|---|
| 1 | **In-memory strokes** | `idsRef`, `PendingSliceBuffer`, `SliceHistory` | no | what the user sees right now |
| 2 | **Browser recovery draft** | IndexedDB | survives refresh/crash, not the machine | re-offering edits after a reload |
| 3 | **Server working mask** | `<MITO_DATA_ROOT>/<project>/<dataset>/<stem>_mask.tif` | yes | the annotator's current draft of the labels |
| 4 | **Operation log** | `AnnotationOperation` rows | yes | audit, ordering, undo intent, the cursor |
| 5 | **Voxel deltas / snapshots** | files under `MITO_DATA_ROOT`, indexed in PostgreSQL | yes, bounded | what undo and recovery actually restore |
| 6 | **Submitted / reviewed history** | submissions, `ReviewRecord`, official label | yes, immutable | what a reviewer approved |

Deployment/database backups are a seventh thing entirely and are an operator
concern (DEPLOYMENT.md), never a recovery mechanism the application relies on.

Three rules follow directly:

- **(3) is a draft, (6) is not.** Autosave writes (3) and never (6). The
  official label changes only on approval. This is the invariant the whole
  phase rests on: autosaving is safe *because* it cannot promote anything.
- **(4) records intent; (5) restores bytes.** ADR-005 already admits an inverse
  row cannot restore a brush stroke. Undo is (4) *plus* (5), never (4) alone.
- **(2) is an offer, not a truth.** A recovered draft is proposed to the user
  and validated against (3)'s version before it can be applied.

## 2. State machine

```
                    ┌──────────────── recovery available ◄── draft found on load
                    │                        │
                    │                   (user accepts)
                    ▼                        ▼
   ┌────────► clean ──── edit ──► locally dirty ──► autosave scheduled
   │            ▲                      ▲   │              │
   │            │                      │   │         (debounce elapsed,
   │        (ack, no                   │   │          no open stroke)
   │      newer revision)              │   │              ▼
   │            │                      │   └── edit ──► saving ──┬──► saved ──► clean
   │            │                      │                         │
   │            └──── newer revision ──┘                    ┌────┴────┐
   │                  survived the ack                      │         │
   │                                                   save failed  stale /
   │                                                        │      conflicted
   └──────────── (retry succeeds) ──────────────────────────┘         │
                                                                 (reload plane,
                                                                  re-offer edits)
                                                                      │
                                                                 recovering
```

| State | Meaning | Exit |
|---|---|---|
| `clean` | no unsaved pixels anywhere | an edit → `locally dirty` |
| `locally dirty` | ≥1 slice in `PendingSliceBuffer` | debounce → `autosave scheduled` |
| `autosave scheduled` | timer armed, no open stroke | fire → `saving`; new edit → re-arm |
| `saving` | one batch in flight, snapshot frozen | all acked → `saved`; partial → `locally dirty`; error → `save failed`; version mismatch → `stale/conflicted` |
| `saved` | server acked every revision sent | → `clean` |
| `save failed` | transport/5xx/400; **pending retained** | slow retry, or manual Save |
| `stale/conflicted` | server version ≠ `expected_version` | reload affected planes → `recovering` |
| `recovery available` | a draft exists for this task on load | accept → `recovering`; discard → `clean` |
| `recovering` | applying draft/reloaded planes | → `locally dirty` or `clean` |

`saving` is deliberately **not** re-entrant. There is one in-flight batch; a
second trigger joins it.

## 3. Autosave timing

| Question | Decision | Why |
|---|---|---|
| When does it start? | On a *completed* edit, never mid-stroke | `SliceHistory.hasOpenStroke` already distinguishes them; flushing mid-stroke would save a half-drawn line and fight the pointer |
| Coalescing | Debounce from the last completed edit | A burst of ten strokes is one request |
| Continuous painter | A ceiling forces a flush even while editing continues | Otherwise an hour of steady painting is never durable — the exact failure the phase exists to prevent |
| New edit during `saving` | Joins the **next** batch | Its slice gets a newer revision; `PendingSliceBuffer.acknowledge` refuses to let the older response clear it. Joining the current batch would mean sending bytes the client already superseded |
| Overlapping timers | One in-flight batch; second trigger returns the same promise | Prevents duplicate writes to one plane |
| Navigation (z) | Freeze the leaving slice, keep it pending; do **not** force a flush | Navigation is not a commit; forcing writes on every page-turn is how the buffer becomes chatty |
| Axis change | Pending edits belong to plane indices that mean something else on the new axis. Flush first; only if the flush fails ask the user | Under the flag this is a *flush*, not a discard — with autosave on there is no reason to lose them |
| Whole-volume ops | Flush first and abort on failure — unchanged from stabilisation | The server reads the mask from disk |
| Refresh / close | `beforeunload` warning stays; a request cannot be guaranteed to finish | Honest: the draft store is what actually saves this case |

## 4. Voxel recovery mechanism — bounded reverse deltas

**Decision: bounded reverse deltas, with periodic snapshot boundaries, stored as
files under `MITO_DATA_ROOT` and indexed in PostgreSQL.**

Doc 22 offers `Snapshot` and `ChunkDelta`; its undo preference is "brush →
restore previous chunk region", which is a reverse delta. Both are adopted, in
the roles they are actually good at:

- **Reverse delta, per operation.** Before applying an operation, the *prior*
  content of exactly the region it touches is written out (RLE for label data —
  the codebase already has `encode_label_rle`, and label planes are extremely
  run-friendly). Undo = apply the reverse delta and append the inverse
  operation. This makes ADR-005 §6's "no stored prior state" failure mode
  disappear for the operations that matter.
- **Snapshot boundary, every N operations.** A compaction point so a long
  history does not need an unbounded delta chain, and so recovery has a floor.

Rejected alternatives:

| Alternative | Why not |
|---|---|
| Full-volume snapshot per stroke | A multi-GB mask copied per stroke. Phase 8 measured 423 ms vs 2.13 ms for dense vs bounded on a *single 1024² plane*; whole volumes are orders worse. Not a mechanism, an outage. |
| Forward deltas only | Recovery would need replay from creation, and undo would need inversion that ADR-005 already showed is not computable for brushes. |
| Voxels in PostgreSQL | Forbidden by ADR-005 conflict B, and would put gigabytes in the control plane. |
| Rely on the op log alone | Explicitly rejected by the brief: an inverse row is not voxel recovery. |

**Bounds are part of the design, not an afterthought:** a cap on retained
deltas per task by count *and* by bytes, oldest-first eviction down to the last
snapshot boundary, and deletion of a task's delta chain when its submission is
approved (state 6 supersedes state 3). Every delta file lives under the
instance-owned data root and therefore passes `core.data_root.assert_owned` —
the guardrail added during stabilisation applies unchanged.

## 5. Dedup, versioning, conflict

Inherited from Phase 7 rather than reinvented:

- `expected_version` — the cursor the client last saw. Mismatch → conflict with
  rebase data, nothing written.
- `idempotency_key` — one per autosave batch. Replay returns the original result
  **without re-applying**; the same key with different parameters is rejected.
  A retried batch after a network drop is therefore safe by construction.
- `payload_digest` — corruption detectable without replay.

A batch is atomic: operations appended and deltas written in one transaction, or
neither. Phase 8's `test_write_failure_rolls_back_the_operation` is the
precedent.

## 6. Multi-tab

Single-owner with explicit takeover. A tab records ownership of a task's draft
(id + heartbeat); a second tab opening the same task sees the existing owner and
opens read-only-for-recovery, offering an explicit takeover rather than silently
racing. Last-write-wins is rejected: two tabs both flushing the same plane is
precisely how one tab's work disappears with a 200.

## 7. Recovery age and cleanup

| Rule | Value |
|---|---|
| Draft is offered | only if its task version is still reachable from the server cursor |
| Max draft age | bounded; older drafts are discarded, not applied |
| Draft dropped | as soon as the server acks a revision ≥ the draft's |
| Delta chain trimmed | to the last snapshot boundary, under count and byte caps |
| Chain deleted | on approval of a submission for that task |

Applying a stale draft to a volume that has moved on is worse than discarding
it, because it silently reverts someone else's accepted work. Discarding is
loud; reverting is not.

## 8. Layering

Four separable pieces, so Phase 11 can replace the bottom one:

```
React editor state        (PendingSliceBuffer / SliceHistory — exists)
        │
persistence service       (draft store: IndexedDB driver behind an interface)
        │
save orchestrator         (batching, debounce, retry, conflict — server-agnostic)
        │
storage driver            (working TIFF + delta files today; chunk store in 11-13)
```

## 9. Rollout

`FEATURE_AUTOSAVE_RECOVERY`, default **False**. Flag off ⇒ behaviour byte-identical
to the stabilised manual-Save semantics: nothing is written until the user clicks
Save, no draft is persisted, no delta is recorded. Endpoints are registered
unconditionally and return 503 when off, per the Phase 3/6 convention.

Migrations are required for the delta/snapshot **index** tables only. No voxel
column, and no conversion of existing annotation state — legacy tasks are valid
with an empty chain, exactly as Phase 7 left them with an empty log.

## 10. Acceptance criteria

1. Flag off → byte-identical to today, asserted not assumed.
2. Flag on → an edit survives refresh, verified by **mask hash**.
3. Undo after autosave restores prior **voxels**, verified by hash.
4. Soak: many edit → autosave → reload cycles with injected 400/409/500,
   network drops and a backend restart; zero lost edits, zero duplicate
   operations, zero corruption; cycle count and duration recorded.
5. External registered sources byte-identical throughout.
6. Two tabs never both apply one draft.
7. Bounds hold: retained deltas stay under the configured count and byte caps
   under sustained editing.

## 11. Known limits, stated up front

- **Tab close cannot be made lossless.** A browser will not wait for a request.
  The draft store narrows the window; the `beforeunload` warning is the honest
  remainder.
- **Recovery is per-task, single-user.** No concurrent merge; conflicts are
  detected and surfaced, not resolved automatically.
- **Delta chains are bounded**, so undo depth is finite by design. The bound is
  configurable and reported, not silent.
- **The chunk store is Phase 11–13.** The storage driver here is the working
  TIFF plus delta files; the interface exists so that can be swapped.
