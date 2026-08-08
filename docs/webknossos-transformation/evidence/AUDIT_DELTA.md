# AUDIT_DELTA — Claude Code re-verification of the Cursor research pack

**Date:** 2026-07-27
**Mode:** read-only verification. No production code, data, or config was modified.
**Scope:** Section C of `CLAUDE_CODE_MASTER_PROMPT.md` — re-verify every Cursor claim against local clones before Phase 0.

## Summary

| Result | Count |
|---|---|
| Claims verified as stated | 26 of 30 (E01–E30) |
| Claims **corrected** (materially wrong) | 1 (E12 upper-bound constraint) |
| Claims needing a **pointer update** (right conclusion, stale path/name) | 2 (E12 identifier, E27 tag reachability) |
| Claims **resolved** that the pack left open (`INFER`) | 1 (E10 tie-break) |
| Blocking contradictions | **0** |

The research pack is **sound enough to build on**. No architectural recommendation changes as a result of this audit. The corrections below matter at implementation time (Phase 3), not at approval time.

---

## 1. Clones and provenance

| Item | Pack claim | Verified | Status |
|---|---|---|---|
| Main WK app cloned | must clone to `external-research/webknossos` | cloned; HEAD `a24aecc6f` (2026-07-27) | done |
| WK license | AGPL-3.0 | `LICENSE` = GNU AGPL v3 | OK |
| `webknossos-libs` HEAD | `0419d102` | `0419d102710e428f245cf2d520b7a0ee33e1d4a5`, clean, `origin/master` | OK |
| `webknossos` pkg license | AGPL-3.0 | `webknossos/LICENSE` = AGPL | OK |
| `cluster_tools` license | MIT | `cluster_tools/LICENSE` = MIT | OK |
| mito-data-agent license | none found | no `LICENSE`/`NOTICE` at repo root | **OK — confirmed gap** |
| WK modules | app, datastore, tracingstore, fossildb | all present + `webknossos-jni`, `webknossos-slick-codegen` | OK |

All nine WK source paths cited in doc `01` exist at the cloned HEAD.

---

## 2. Corrections to the pack

### 2.1 E12 — `pendingInstances` upper bound is **not** enforced (materially wrong)

Doc `04` and E12 claim: *"CHECK constraints: `0 ≤ pendingInstances ≤ totalInstances`"*.

Only the **lower** bound exists in current WK:

```sql
-- schema/schema.sql, table webknossos.tasks
CONSTRAINT pendingInstancesLargeEnoughCheck CHECK (pendingInstances >= 0)
```

History: `008-task-instances-triggers.sql` added `openInstancesSmallEnoughCheck (openInstances <= totalInstances)`; **`026-decrease-total-instance.sql` deliberately dropped it** so `totalInstances` could be decreased below the already-claimed count.

**Why it matters (Phase 3):** the safety property WK actually relies on is the *lower* bound plus the Serializable retry — not a two-sided invariant. If mito adds a `pending <= total` CHECK, reducing `total_instances` on a partly-claimed task will fail at the DB. Mirror WK: enforce `>= 0` only, and handle over-reduction in the service layer.

### 2.2 E12 — cited file uses the old identifier

Evolution `008` predates the rename and says `openInstances`. `107-task-terminology.sql` renamed it to `pendingInstances`. **Implementers should read `schema/schema.sql` (current trigger + constraint truth), not evolution 008.** The trigger *behavior* the pack describes is otherwise accurate.

### 2.3 E27 — release tag not reachable from master

Pack says latest release `26.08.0`. The tag exists, but `26.08.0` is **not an ancestor of master** (release tags live on release branches); `git describe` from HEAD yields `26.04.0`. Not a factual error, but "HEAD == 26.08.0" would be wrong. **Pin analysis to commit `a24aecc6f`, not to a release tag.**

### 2.4 E10 — open `INFER` resolved

Doc `04` flagged uncertainty over whether a random tie-break was added among equal-priority projects. Verified in `app/models/task/Task.scala`: `findNextTaskQ` orders by project priority only, `LIMIT 1`, **no `RANDOM()`**. The DOC's "random among equal priority" is not implemented in SQL. mito should specify its own deterministic tie-break (doc `17` already does).

---

## 3. WK claims verified as stated

| ID | Claim | Evidence at HEAD `a24aecc6f` |
|---|---|---|
| E09 | `request` assigns via `assignNext` | `TaskController.scala` |
| E10 | Eligibility SQL joins experiences, filters pending, orders by priority | `Task.scala:113` `findNextTaskQ` |
| E11 | Serializable isolation + 50 retries | `Task.scala:167-169`, `196-198` — `withTransactionIsolation(Serializable)`, `retryCount = 50`, `retryIfErrorContains = List(transactionSerializationError, "Negative pendingInstances for Task")` |
| E12 | Counter maintained by triggers on annotation insert/update/delete | `schema.sql:1087-1120`, `countsAsTaskInstance` — *see corrections above* |
| E13 | `maxOpenPerUser` gates the eligible team set | `TaskService.scala:65-74`, `Msg.Task.tooManyOpenOnes` |
| E14 | Interpolation = SDF + linear blend, active id, depth cap 100 | `volume_interpolation_saga.ts` — `MAXIMUM_INTERPOLATION_DEPTH = 100` (:42), `signedDist` (:235), `absMax` (:195), `weightedAverage = firstVal*(1-k) + lastVal*k` / `shouldDraw = weightedAverage < 0` (:447-448), `distance-transform` import (:2), `labelWithVoxelBuffer2D` (:459) |
| E16 | PullQueue priority/abort/batch | `pullqueue.ts` — `BATCH_LIMIT: 6` (:20), `BATCH_SIZE = 6` (:22), `AbortController` (:48), `PriorityQueue` (:30) |
| E17 | Datastore serves multi-bucket binary data | `BinaryDataController.scala` present |

E14's algorithm description is **accurate line-for-line** — the interpolation port (Phase 8) can proceed on the pack's description as written.

---

## 4. mito-data-agent claims verified

| Claim | Verified |
|---|---|
| Django 5 + DRF + **SQLite** | Django 5.1.15, Python 3.11.15, `ENGINE: sqlite3` (`settings.py:106`) |
| ~290 backend tests | exactly **290** collected |
| No frontend tests | no `*.test.*` / `*.spec.*` under `frontend/src` |
| No Docker / compose | none in tree |
| `MAX_OPEN_VOLUMES = 8` memmap LRU | `slice_io.py:32` |
| Undo = 20 full-slice snapshots | `AnnotationCanvas.tsx:61` `MAX_UNDO = 20`, `undoStack: Int32Array[]` (:492) |
| No autosave (manual Save only) | no autosave references in `frontend/src` |
| No interpolation | only a comment noting its absence (`cellable_port/label_state.py:14`) |
| Task = single assignee FK | `AnnotationTask.assigned_to` FK (`annotation/models.py:32`); no instance/type entities |
| Review/lock loop mature | `approve_submission`, `annotation_locked` gate (`services.py:594-641`) |
| `ProcessingJob.on_job_finished` is a placeholder | `processing/services.py:198` — `# Placeholder hook`, `return None` |
| EfficientSAM + SAM2 vendored | `vendor/efficient_sam`, `vendor/sam2` |
| "Format enum lies" | `core/choices.py` offers `zarr`/`hdf5`/`n5`; `slice_io.py` only ever opens `tifffile` / `nibabel` |

**Model inventory (11 models, all confirmed):** `Institution`, `UserProfile`, `AnnotatorProfile`, `Project`, `Dataset`, `Volume`, `AnnotationTask`, `AnnotationSubmission`, `HardCase`, `ReviewRecord`, `ProcessingJob`.

**Addition not in the pack:** `AnnotationTask` already carries a full bounding box (`z/y/x_start`, `z/y/x_end`), not just a z-range as doc `13` implies. This *helps* Phase 2 — the WK `Task.boundingBox` concept already has a home and needs no new columns.

---

## 5. Working-tree state (safety requirement D)

`git status` at audit time — **82 dirty paths, no stashes**, branch `main` @ `83b547c`:

- 61 modified tracked files (+3637 / −1004)
- 21 untracked, including new features (`accounts/services.py`, `test_people.py`, `HardCasesPage.tsx`, `PeoplePage.tsx`, `hardCases.ts`, `people.ts`) and 2 unapplied migrations
- 5 `*.backup.<timestamp>` files (`label_paths.py`, `settings.py`, `urls.py`, `views.py`, `LoginPage.tsx`)

This is live in-progress work on hard-cases / people / submit-loop, exactly as doc `00` warned. **It has not been touched, stashed, or committed.** All verification used read-only commands plus a detached `git worktree` in scratch space.

**Recommendation before Phase 1:** commit or stash this WIP. Expand-contract migrations on `accounts` and `annotation` will collide with the two pending untracked migrations (`accounts/0003_*`, `annotation/0006_*`).
