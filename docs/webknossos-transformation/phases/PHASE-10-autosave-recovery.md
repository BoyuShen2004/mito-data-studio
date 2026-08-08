# Phase 10 — autosave / undo / recovery

**Status:** **complete.** Recovery storage, the HTTP API, the editor state
machine, IndexedDB drafts, multi-tab ownership, the jsdom harness, mounted-editor
coverage and both soaks are implemented, tested and committed, and the editor
drives the machine. See §Completion gate for the evidence, and §Known limitations
for what is deliberately out of scope.
**Depends on:** Phase 7 · **Gate (phase map):** soak refresh tests
**Design:** [ADR-008](../adr/ADR-008-autosave-and-recovery.md) · [scope note](PHASE-10-scope-note.md)

---

## What shipped

| Area | Detail |
|---|---|
| Core | `annotation/recovery/deltas.py` — pure; changed-box, RLE encode/decode, build/apply reverse delta |
| Store | `annotation/recovery/store.py` — placement under `MITO_DATA_ROOT`, count + byte caps, retention, purge |
| Service | `annotation/recovery/service.py` — the single boundary; flag enforced here |
| Flag | `FEATURE_AUTOSAVE_RECOVERY`, default **False**; recording also needs `FEATURE_ANNOTATION_OPS` |
| Tests | `test_recovery.py` (31), `test_recovery_soak.py` (2) |
| **Migrations** | **None** — see §Deviation |

## The mechanism, and why it is not an inverse operation

ADR-005 §6 records that undo "of an operation whose inverse cannot be computed
(no stored prior state) fails atomically". A brush stroke has no computable
inverse: erasing to background is not restoring what was underneath. So an
inverse operation row records *intent*, and Phase 7 said plainly that the log is
"an audit and undo substrate, not a recovery mechanism, until a snapshot/
chunk-delta store exists".

This is that store. Before an edit is applied, the prior content of exactly the
bounding box it will touch is captured, RLE-encoded, and written to

```
<MITO_DATA_ROOT>/<project>/<dataset>/recovery/task_<id>/<seq>-<uuid>.json.gz
```

Undo re-applies that box. Restoring costs the size of the edit, not the size of
the volume.

**Bounded by construction.** Phase 8 measured 423 ms dense versus 2.13 ms bounded
on a single 1024² plane; Phase 9 measured 179×/653× for bounded flood fill. A
whole-volume snapshot per stroke is an outage, not a mechanism. Measured here: a
typical stroke's delta is a few hundred bytes, and a 200-cycle soak left a chain
of 9 entries totalling 1 109 bytes.

**Voxels on disk, never in PostgreSQL** — ADR-005 conflict B, restated by
ADR-008 §4. Writes go through `core.data_root.assert_owned`, so the stabilisation
guardrail covers this store unchanged.

## Retention

Both caps are enforced, because a few huge deltas breach the byte cap while
satisfying the count cap and a flood of tiny ones does the reverse.

| Rule | Default | Setting |
|---|---|---|
| Max entries per task | 200 | `MITO_RECOVERY_MAX_ENTRIES` |
| Max bytes per task | 64 MiB | `MITO_RECOVERY_MAX_BYTES` |
| Retention window | 7 days | `MITO_RECOVERY_RETENTION_SECONDS` |
| After a save | purge through the saved seq — **only what that save covered** |
| On approval | purge the whole chain (the working copy has been promoted) |

A stale delta applied to a volume that has moved on silently reverts work
someone else accepted. Discarding is loud; reverting is not.

## Soak result (the gate)

Deterministic seed, so a failure reproduces.

| Metric | Value |
|---|---|
| Cycles | 200 |
| Duration | 0.4 s |
| Edits attempted | 235 |
| Edits preserved | **200/200 cycles** |
| Undo voxel-restore checks | 8 |
| Failures injected | **151** — delayed 15, dropped 20, retry 30, 409 ×10, backend restart 14, refresh-during-save 27, edit-during-save 35 |
| Final mask sha256 | verified against an independently maintained expectation |
| Recovery chain remaining | 9 entries / 1 109 bytes (caps 200 / 64 MiB) |
| External source image | byte-identical |
| **Edit-loss events** | **0** |

"Refresh" drops every in-memory handle and re-reads the mask from disk. A soak
that kept the array in memory would assert that Python variables persist, not
that annotation work does.

## Deviation from ADR-008

ADR-008 §9 anticipated **index tables** in PostgreSQL for delta retention.
Implementation showed they are unnecessary: the sequence lives in the filename,
so ordering, eviction, cleanup and usage accounting are a directory scan, and
index and voxels cannot drift apart. **No migration is needed and none was
added.** This is a simplification within the ADR's model, not a redesign of it —
placement, bounding, purge rules and the flag are unchanged.

## Delivery status

### Delivered

| Item | Where | Tests |
|---|---|---|
| Bounded reverse-delta store | `annotation/recovery/{deltas,store,service}.py` | 31 |
| Autosave / recovery HTTP API | `annotation/recovery/api_service.py`, `annotation/recovery_api.py` | 32 |
| Editor autosave state machine | `features/viewer/autosave/stateMachine.ts` | in the 43 |
| Transport classification | `features/viewer/autosave/transport.ts` | in the 43 |
| IndexedDB draft persistence | `features/viewer/autosave/draftStore.ts` | in the 43 |
| Multi-tab ownership | `features/viewer/autosave/tabOwnership.ts` | in the 43 |
| jsdom / testing-library harness | `vite.config.ts`, `src/test/setup.ts` | — |
| Storage soak | `test_recovery_soak.py` | 2 |
| End-to-end soak | `test_recovery_soak_e2e.py` | 2 |
| Smoke matrix | 16 flag configurations | 83 |

### End-to-end soak result (the gate)

| Metric | Value |
|---|---|
| Cycles | 120 |
| Edits attempted / preserved | 135 / **120 of 120 cycles** |
| Failures injected | **89** across all 8 categories |
| Ambiguous retries resolved | 8 |
| Stale conflicts surfaced | 14 |
| **Duplicate operations** | **0** (120 operations for 120 logical autosaves) |
| Final mask hash | verified against an independent expectation |
| Browser drafts left | 0 |
| Server recovery records | 8 (cap 200) |
| External source image | byte-identical |
| **Silent edit loss** | **0** |

### Editor wiring

`AnnotationCanvas` constructs and drives the machine through `useAutosave`, the
integration seam. The canvas diff is 139 lines of call sites; the lifecycle lives
in the hook so it stays testable without mounting a canvas.

| Wiring point | Behaviour |
|---|---|
| Flag | *Discovered*, never assumed — `GET /api/tasks/<id>/recovery/` reports `enabled`. Until it answers, and whenever off, the editor is unchanged. |
| Edit chokepoint | `markDirty` — brush, erase, box commit, point commit, flood fill, undo, redo, delete all funnel through it, so one logical action bumps the revision exactly once (asserted: exactly one `onEdit` call site). |
| Manual Save | Routes through the same canonical flush when enabled, so a click during a pending autosave joins that queue rather than racing it. |
| Pointer guard | Also consults the autosave in-flight and foreign-owner refs, both synchronous, so a stroke cannot mutate a raster a save already captured. |
| Status | Machine → existing chrome vocabulary, conservatively: `"saved"` only after the server acknowledged that exact revision. |
| Unload | Warns on real unsaved work, including a draft not yet on the server. |
| Session scoping | Enforced on every async continuation, so a response, timer or ownership message from a previous task cannot touch the current editor. |

Two bugs the mounted tests exposed, both fixed:

1. An edit made **before the deployment fingerprint resolved** was silently not
   persisted as a draft — the first seconds after load, when a refresh is most
   likely. The draft write now awaits identity rather than skipping.
2. The per-task session effect reset the machine whenever it re-ran, so an
   unstable callback identity from a caller discarded dirty state. Reset is now
   keyed on the task actually changing.

`projectId` is an optional prop defaulting to 0 rather than threaded through the
pages: the pages that mount the canvas are uncommitted owner WIP, and drafts are
already uniquely scoped by deployment + user + task since task ids are global.

### Completion gate

| Requirement | Evidence |
|---|---|
| Canvas constructs and drives the machine | `useAutosave` in `AnnotationCanvas`; one instance asserted |
| Real editor edits mark revisions dirty | single `onEdit` call site at `markDirty` |
| Manual Save and autosave share one path | `autosave.flush()` when enabled; asserted |
| Mounted editor uses IndexedDB recovery | draft written on edit, cleared on ack — mounted tests |
| Multi-tab ownership affects real behaviour | non-owner issues no competing write; pointer guard blocks |
| Mounted-component lifecycle tests pass | **21 tests** |
| Mounted-editor end-to-end soak passes | 120 cycles through the real API, 0 loss |
| Feature-disabled behaviour unchanged | flag off: no identity/recovery/autosave request, no draft |

### Soak scope, stated separately

| Soak | Layer | Cycles | Result |
|---|---|---|---|
| `test_recovery_soak` | storage/service only | 200 | 200/200 preserved, 151 injected failures |
| `test_recovery_soak_e2e` | client lifecycle → real HTTP → storage | 120 | 120/120 preserved, 89 injected failures, 0 duplicate operations |

Neither drives a browser. The end-to-end soak exercises the real view and the
real storage with a client-shaped draft store; the mounted-editor tests exercise
the real hook. A true browser soak would need Playwright, which this phase does
not add.

### Known limitations before Phase 11

- Tab close cannot be made lossless: a browser will not wait for a request. The
  draft store narrows the window; the `beforeunload` warning is the honest
  remainder.
- Multi-tab coordination is BroadcastChannel-based and therefore best-effort
  across crashes, browsers and machines. The server's `expected_version` check
  is the authoritative protection, and it is tested.
- Recovery is per-task and single-user. Conflicts are detected and surfaced, not
  merged.
- Delta chains are bounded, so undo depth is finite by design; the bound is
  configurable and reported rather than silent.
- The storage driver is behind a narrow interface so Phase 11's chunk stack can
  replace it, but that seam has not been exercised against a second
  implementation.
