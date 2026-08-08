# Phase 10 — scope note (autosave / undo / recovery)

**Status:** scope established, design in [ADR-008](../adr/ADR-008-autosave-and-recovery.md).
**Implementation:** not started — see §Delivery status.
**Depends on:** Phase 7 (per phase map).
**Gate (phase map):** **soak refresh tests**.

---

## 1. Authoritative sources, read in full

| Source | What it fixes for this phase |
|---|---|
| `CLAUDE_CODE_MASTER_PROMPT.md` §E10 | The whole brief: *"Autosave batches; IndexedDB draft optional; refresh recovery; save chrome indicator; soak kill-tab tests."* |
| `27-claude-code-phase-map.md` row 10 | `Autosave/undo/recovery | depends 7 | gate: soak refresh tests` |
| `22-target-persistence-and-recovery.md` | The **model**: `Snapshot / OperationLog[] / ChunkDelta[] / SaveCursor`, the write path, undo/redo preference, and the recovery matrix |
| `19-target-annotation-design.md` | Ranks *"Autosave + recovery"* **P0**, rationale *"Don't lose strokes on refresh"*, inspiration WK PushQueue |
| `ADR-005` (Phase 7) §5, §6 | The op log that already exists, and **what it deliberately does not do** |
| `25-testing-benchmark-and-soak-strategy.md` | Soak layer: *"4h single-session editor; 2h multi-user"*; no phase done without its acceptance tests green |
| `PHASE-9-annotation-tools.md` | Records that undo does not restore voxels and that inventing a mechanism there would pre-empt this phase |
| Current code | `AnnotationCanvas.tsx`, `pendingSliceBuffer.ts`, `sliceHistory.ts`, `revisionedFetch.ts`, `annotation/services.py` save path, `annotation/operations/` |

**Phase 10 is not inferred from its title.** The three nouns in that title mean
specific things in doc 22, and two of them already partly exist.

## 2. What already exists (and must not be rebuilt)

Phase 7 shipped the operation log: `AnnotationOperation`, append-only, with
`expected_version`, `idempotency_key`, `payload_digest`, `inverse_of`,
`undone_at`, and the cursor materialised as `MAX(seq)` rather than a stored
column. That **is** doc 22's `OperationLog[]` and `SaveCursor`.

ADR-005 §5 states the consequence without hedging:

> the log is an *audit and undo* substrate, not a recovery mechanism, until a
> snapshot/chunk-delta store exists. Full replay-based recovery needs the zarr
> chunk store doc 22 describes, which is **not** in this phase.

The stabilisation pass that preceded Phase 10 also shipped, on the frontend:
`PendingSliceBuffer` (revisioned per-slice pending edits), `SliceHistory`
(per-slice undo/redo with no-op rejection), and `RevisionedFetch` (stale-read
invalidation). Those are the *in-memory* half of this phase and are already
correct; Phase 10 adds durability beneath them, it does not replace them.

## 3. Required features

1. **Autosave batches.** Completed edits flush to the server working copy
   without an explicit Save, coalesced so a burst of strokes is one request.
2. **Save chrome indicator.** The editor states, truthfully, which of
   clean / dirty / saving / saved / failed / conflicted / recovering it is in.
3. **Refresh recovery.** A browser refresh with unflushed edits must not lose
   them. Doc 22's mechanism: load snapshot + ops after cursor, and restore the
   unsaved local queue from IndexedDB *if present*.
4. **Bounded snapshot / chunk-delta store** so undo and recovery restore real
   voxels. This is the piece Phase 7 deferred and Phase 9 refused to invent.
5. **Server-side crash resilience.** Durable DB + working copy; a client
   resumes from the cursor after a backend restart.
6. **Conflict handling** on flush: verify versions, and on mismatch rebase or
   reload the affected planes and tell the user.

## 4. Excluded features (and why)

| Excluded | Reason |
|---|---|
| Zarr3 pyramid / chunk service | Phases 11–13 (`20`, `21`). Doc 22's "zarr chunk store" wording refers to that stack; Phase 10 must not pre-build it. |
| Append-only submission migration | Doc 22 §Submission history is a separate migration, gated on backfill, and touches review semantics owned by Phase 5. |
| Multi-user live co-editing | Nothing in E10 or doc 22 asks for presence or CRDTs. Conflict handling here is *detect and recover*, not *merge concurrently*. |
| Offline editing | Not in E10. IndexedDB is named as a **draft** store for recovery, not an offline mode. |
| Deployment integration | Explicitly out of scope for this phase. |
| Full-volume snapshot per stroke | Rejected on cost — see §7. |

## 5. Exact autosave behaviour

| Question | Decision |
|---|---|
| Trigger | One *completed* edit — stroke end, AI commit, undo, redo, delete slice/instance. Not per pointer-move. |
| Debounce | Coalesce a burst into one flush. Idle → flush promptly; still actively painting → keep coalescing, with a ceiling so a continuous painter still gets durability. |
| Active vs idle | An open stroke never triggers a flush; `SliceHistory.hasOpenStroke` already models this. |
| Save in flight | A new edit does **not** join the in-flight request. Its slice gets a newer revision and is picked up by the *next* flush — `PendingSliceBuffer.acknowledge` already enforces this. |
| Overlapping timers | One flush at a time, serialised; a second trigger joins the existing promise rather than issuing a duplicate write. |
| Failure | Pending edits are retained and retried on a slower cadence; the indicator says failed; manual Save remains available. |
| Flag off | **Byte-identical to today**: nothing is written until the user clicks Save. |

## 6. Recovery behaviour

| Event | Behaviour |
|---|---|
| Page refresh | Pending edits persisted client-side are offered for recovery; server state is snapshot + ops after cursor. |
| Tab close | Best-effort persist; the `beforeunload` warning stays as the honest fallback (a request cannot be guaranteed to complete). |
| Frontend crash | Same path as refresh — whatever reached the draft store. |
| Backend restart | Durable working copy + op log; the client resumes from `MAX(seq)`. |
| Network drop | Queue locally, exponential backoff, version check on flush. |
| Max recovery age | Bounded; stale drafts are discarded rather than silently applied to a volume that moved on. |
| Cleanup | Draft entries are dropped once the server acknowledges the revision that superseded them. |
| Multi-tab | Two tabs on one task must not both claim to own the draft. Single-owner with explicit takeover, not last-write-wins. |

## 7. Voxel recovery — the substantive requirement

**An inverse operation alone is not voxel recovery.** ADR-005 §6 appends an
inverse and admits that undo of an operation "whose inverse cannot be computed
(no stored prior state) fails atomically". For brush strokes there *is* no
computable inverse without prior state — erasing to background is not the same
as restoring what was there.

So Phase 10 must store prior state, bounded. Doc 22 offers both options:
`Snapshot (periodic compact label chunks)` and `ChunkDelta[] (chunk_coord →
bytes or RLE, version)`, with undo/redo preferring "op-log invert functions
(brush → restore previous chunk region)" and "fallback snapshot boundaries
every N ops".

Constraint inherited from ADR-005 conflict B: **no voxel payloads in
PostgreSQL.** Voxel bytes live on disk under `MITO_DATA_ROOT`; the database
stores coordinates, versions, digests and sizes. Any store added here must obey
that, and must live under the instance-owned data root enforced by
`core/data_root.py`.

Bounding is mandatory, by count and by bytes, with cleanup of superseded and
abandoned entries. A full-volume snapshot per minor stroke is rejected: Phase 8
measured a 1024² plane at 423 ms dense versus 2.13 ms bounded, and Phase 9
measured 179× / 653× for bounded flood fill. Per-stroke whole-volume copies of
a multi-gigabyte mask are not a mechanism, they are an outage.

## 8. Operation-log relationship

Autosave writes voxels **and** appends operations; the log stays the audit and
undo spine. The delta/snapshot store is what makes the log's undo *effective*
rather than merely recorded. `expected_version` and `idempotency_key` are
already the contract for dedup and conflict, so a retried autosave batch is
idempotent by construction and does not need a new mechanism.

## 9. Conflict, dedup, versioning

- `expected_version` = the op cursor the client last saw; a mismatch is a
  conflict, reported with rebase data, nothing written.
- `idempotency_key` per batch: replay returns the original result **without
  re-applying**; the same key with different parameters is rejected.
- A rejected write must leave pending edits intact — already true, and now also
  true for the 400 the slice-index guard raises.

## 10. Surfaces

- **Flag:** `FEATURE_AUTOSAVE_RECOVERY`, default **False**. No authoritative
  name exists in the master prompt (E10 names none, exactly as E9 named none for
  `FEATURE_ANNOTATION_TOOLS`), so the phase names its own, following convention.
- **Migrations:** required for the snapshot/delta index tables. None for voxels.
- **APIs:** an autosave batch endpoint alongside the existing
  `PUT /api/tasks/<id>/label-ids/`, plus recovery read endpoints. Registered
  unconditionally, returning 503 when the flag is off, per the Phase 3/6
  convention.
- **Frontend:** persistence service separate from React state, separate from the
  save orchestrator, separate from the storage driver.

## 11. Acceptance criteria (the gate)

The phase gate is **soak refresh tests**, so the criteria are behavioural:

1. Autosave disabled → save behaviour byte-identical to today.
2. Enabled → an edit survives a refresh, verified by **mask bytes or hash**,
   not by a 200.
3. Repeated edit → autosave → reload cycles across many iterations show no lost
   edit, no duplicate operation, no corruption; exact cycle count and duration
   recorded.
4. Intermittent injected failures (400/409/500, network drop, backend restart)
   leave the working copy consistent and the pending queue retryable.
5. Undo after autosave restores the prior **voxels**, not merely an inverse row.
6. External registered source images and labels remain byte-identical
   throughout.
7. Two tabs on one task never both apply the same draft.

## 12. Conflicts found and how they resolve

**A — "no autosave" versus Phase 10 autosave.** The stabilisation pass was
explicitly instructed: *only clicking Save writes to the working copy; do not
implement autosave.* Phase 10's whole subject is autosave. These are reconciled
by the flag, not by choosing a side: with `FEATURE_AUTOSAVE_RECOVERY` off the
stabilised manual-Save semantics are preserved exactly, and autosave exists only
when an operator opts in. This follows Phases 6–9, which all shipped inert.
Least destructive reading: the earlier instruction constrains *default*
behaviour, which it continues to govern.

**B — doc 22's "zarr chunk store" versus Phase 10's dependency list.** Doc 22
describes recovery in terms of a chunk store that Phases 11–13 build. Phase 10
depends only on Phase 7. Resolved by implementing the *bounded snapshot / delta*
form of doc 22's model against the existing working-TIFF storage, so recovery
works now and the later chunk stack can replace the driver without changing the
contract.

**C — "prefer op-log invert functions" versus "no stored prior state".** Doc 22
prefers inversion; ADR-005 records that brush inversion is not computable
without prior state. Resolved in doc 22's own terms: its invert function is
"brush → **restore previous chunk region**", which *is* stored prior state. So
inversion and snapshots are not alternatives — the delta *is* how inversion
becomes possible.

## 13. Relationship to Phase 11

Phase 11 begins the volume IO stack (Zarr3 pyramid derivatives). Phase 10 must
therefore keep its storage behind a narrow driver interface so Phase 11 can
supply a chunk-backed implementation without touching the save orchestrator or
the recovery contract. Phase 10 must **not** introduce a pyramid, a chunk
service, or a token-authenticated chunk endpoint.

## Delivery status

Scope (this note) and design (ADR-008) are complete. **Implementation, tests
and soak runs are not started.** The reasoning is recorded in the final report:
a partially built autosave/recovery layer sits directly on the save path that
the preceding stabilisation pass just repaired, and shipping it half-tested
would risk exactly the class of silent edit loss that pass eliminated.
