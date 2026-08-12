# BASELINE — Phase 0 measurements before any redesign

**Date:** 2026-07-27
**Repo:** `/home/weidf/shenb/mito-data-studio` @ `83b547c` (+82 uncommitted WIP paths)
**Mode:** read-only measurement. No production code, data, DB, or config modified.

Everything below is a **measured number**, not an estimate. Targets in
`21-target-rendering-architecture.md` are quoted for comparison only; they are
provisional and should be re-ratified now that real numbers exist.

---

## 1. Environment

| Item | Value |
|---|---|
| OS / kernel | Linux 5.4.0-216-generic |
| CPU / RAM | 24 cores / 125 GB (94 GB in page cache at measurement) |
| GPU | 4× NVIDIA RTX 2080 Ti (11 GB each), driver 570.207 |
| Python | 3.11.15 (conda env `mito-data-studio`) |
| Django | 5.1.15 |
| Database | **SQLite** `backend/db.sqlite3`, 304 KB, no `OPTIONS` (no WAL, no busy timeout) |
| `MITO_DATA_ROOT` | `./data` (84 KB — dev tree is effectively empty) |
| Dev DB contents | **0 volumes, 0 tasks**, 5 users |
| Providers | QC `basic`, visualization `inapp`, tracking `sam2` |
| Deploy artifacts | none in tree (no Docker/compose/gunicorn config) |

**Benchmark corpus.** The dev DB has no registered volumes, so measurements use
real EM volumes at `/home/weidf/shenb/wk_data` (read-only, outside the repo):

| Volume | Shape (z,y,x) | dtype | MPix per z-slice | File |
|---|---|---|---|---|
| heart/image | 137 × 2758 × 2514 | uint8 | 6.9 | 950 MB |
| **liver/image** | **160 × 3885 × 4544** | uint8 | **17.7** | **2.8 GB** |
| mouse_cortex/image | 256 × 2048 × 2048 | uint8 | 4.2 | 1.4 GB |
| mouse_cortex/mask | 256 × 2048 × 2048 | uint16 | 4.2 | 2.1 GB |

---

## 2. Test suite baseline

Harness: `python manage.py test`. Full logs in `TEST_BASELINE.txt`.

| Tree | Tests | Result | Wall |
|---|---|---|---|
| **Committed HEAD `83b547c`** (isolated worktree) | 248 | **OK — all pass** | 192 s |
| **Working tree (HEAD + WIP)** | 290 | **FAILED — 5 failures, 4 errors** | 291 s |

> **The 9 failures are introduced by the uncommitted WIP, not pre-existing.**
> Verified by running the suite at `83b547c` in a detached `git worktree` in
> scratch space; the working tree was never stashed or altered.

All 9 cluster on one behaviour change — volume registration `label_type` semantics:

| Count | Symptom |
|---|---|
| 3 | `AssertionError: 400 != 201 : {'detail': 'label_type cannot be none when a mask is registered.'}` |
| 1 | `AssertionError: 400 != 201 : {'detail': "label_type 'proofread' is no longer supported; use none, partial, or prediction."}` |
| 1 | `AssertionError: TaskType.MANUAL_ANNOTATION != TaskType.PREDICTION_PROOFREADING` |
| 1 | `KeyError: 'volumes'` |

Failing tests: `annotation.test_api_flows.DataRegistrationFlowTests` (×5),
`volumes.tests.RegisterPairsTests` (×2), `SeparateDirectoryRegistrationTests` (×1),
`CreateTasksTests.test_prediction_volume_makes_proofreading_tasks` (×1).

**This was the gate on Phase 1.** The WIP tightened `label_type` validation and
dropped the `proofread` value without updating callers/tests.

### Resolved in Phase 0.5 (2026-07-27)

| Tree | Tests | Result |
|---|---|---|
| Working tree after Phase 0.5 | **293** | **OK — all pass** (`TEST_PHASE0_5.txt`) |

Three of the nine failures were a genuine product regression, not stale tests:
`register_volume`/`register_dataset` both defaulted `label_type` to `"none"`,
and `volumes/api.py` sent `"none"` when the client omitted the field — which the
new rule then rejected. Since the frontend never sends `label_type` at all,
**every masked registration from the UI returned 400**. Fixed by making
"unspecified" (`None`) distinguishable from an explicit `"none"`: an unspecified
mask defaults to `prediction` (the prior behaviour), while every rule the WIP
added is preserved. The other six tests asserted behaviour the WIP deliberately
changed and were updated; three new tests pin the previously untested rules.

### 2.1 Full coupling sweep (extended on user instruction)

Every code path coupling `label_path` / `label_type` / mask /
`prediction|partial|proofread|none` was audited, not just the nine failures.
**Final suite: 307 tests, all green** (`TEST_PHASE0_5.txt`).

| # | Defect | Severity | Fix |
|---|---|---|---|
| C1 | **Legacy `proofread` rows were uneditable.** `update_volume_metadata` validated the *stored* type, so any edit touching a label field on a legacy row raised — including a plain rename. The retired-value check was applied to history, not just to input. | **High** — silent data lockout | Validation now applies to what a caller *sends*; stored values are carried over untouched |
| C2 | **Attaching a mask via PATCH raised an unactionable error.** `{label_path: "x.tif"}` on an untyped volume failed with "label_type cannot be none when a mask is registered", which does not say what to do. | Medium | Still refuses (per instruction: **never** silently invent `prediction`), but names the field and the two valid values |
| C3 | **The API advertised a value it always rejects.** `RegisterDataSerializer.label_type` offered every `LabelType` including `proofread`, which the service then rejected — a contract that documents an impossible call. | Medium | Write surfaces read from `WRITABLE_LABEL_TYPES`; `proofread` stays in the model for legacy reads |
| C4 | **The invariant had three private copies.** Valid-with-mask was spelled inline in the validator, the serializer, and the choices module, free to drift. | Low | Single source of truth: `WRITABLE_LABEL_TYPES` / `MASKED_LABEL_TYPES` in `core/choices.py` |

**Deliberately not changed:** `_promote_working_label_to_official`
(`annotation/services.py`) sets `label_type` `none → partial` when a manager
approves an in-app submission. That *is* a silent conversion, but a justified
one — an annotator has just produced a real label, and the result satisfies the
invariant. Changing it would alter approval semantics, which is out of scope.

**Rule established:** validation constrains what a caller **sends**; it never
retroactively invalidates what is **stored**. This is the expand-contract
posture the later phases need — new constraints must not strand existing rows.

14 regression tests added (`volumes.tests.LabelTypeCouplingTests`) covering
registration defaults, both rejection cases, mask attach/repoint/remove, and
legacy-row editability.

---

## 3. Volume IO — the viewer hot path

Harness: `bench_slice_io.py`, calling `slice_io.render_image_slice_jpeg()`, i.e.
exactly what `VolumeSliceView` runs per request, minus HTTP/Django overhead.
**These are floor numbers — real requests are strictly slower.**

### 3.1 Latency (single-threaded)

| Measurement | heart (6.9 MPix) | **liver (17.7 MPix)** | Target (`21`) |
|---|---|---|---|
| `volume_meta` cold | 17.9 ms | 15.8 ms | — |
| **Cold first slice (TTFV, server-side)** | 350.8 ms | **637.7 ms** | < 3 s cold ✅ |
| **Sequential scrub p50** | 291.2 ms | **595.9 ms** | — |
| **Sequential scrub p95** | 303.4 ms | **659.2 ms** | **< 100 ms ❌ 6.6× over** |
| Random scrub p95 | 301.4 ms | 615.7 ms | < 100 ms ❌ |
| Uncached scrub p95 | 289.1 ms | 603.5 ms | — |
| Orthogonal Y p50 | 14.4 ms | 19.7 ms | — |
| Orthogonal X p50 | 19.9 ms | 28.3 ms | — |

### 3.2 The bottleneck is encode, not IO

The orthogonal axes are **20–30× faster** than Z, which inverts the usual
memmap expectation (Z is the contiguous axis; Y/X cut across the file).

The reason is pixel count, not locality:

| Axis (liver) | Slice size | MPix | p50 |
|---|---|---|---|
| Z | 3885 × 4544 | 17.7 | 595.9 ms |
| Y | 160 × 4544 | 0.7 | 19.7 ms |
| X | 160 × 3885 | 0.6 | 28.3 ms |

≈ **34 ms per megapixel**, consistent across both volumes and all axes. Cost is
dominated by full-resolution windowing + JPEG encode of a whole plane.

> **Design consequence:** caching, prefetching, and faster disks cannot fix
> this — the work is proportional to pixels encoded. Only **multiresolution
> mags** (encode a downsampled plane while scrubbing) reduce it. This is direct
> empirical support for Phase 11 (pyramids) preceding Phase 13/14, and it means
> the p95 target is reachable: at mag 4, liver's 17.7 MPix plane becomes
> 1.1 MPix ≈ **38 ms**, comfortably under 100 ms.

### 3.3 Concurrency collapse

Same volume, N threads (as gunicorn threads / multiple viewer tabs would be):

| Volume | Threads | p50 | p95 | Throughput |
|---|---|---|---|---|
| heart | 1 | 291 ms | 303 ms | 3.4 slice/s |
| heart | 4 | 2410 ms | 2523 ms | **1.7 slice/s** |
| heart | 12 | 7167 ms | 8643 ms | **1.7 slice/s** |
| liver | 1 | 596 ms | 659 ms | 1.7 slice/s |
| liver | 4 | 3063 ms | 3441 ms | **1.3 slice/s** |
| liver | 12 | 9385 ms | 10896 ms | **1.3 slice/s** |

**Throughput is flat while latency grows linearly with concurrency** — the
signature of a fully serialized resource (GIL-bound encode). A 12th concurrent
slice request waits ~11 s on liver. Adding threads to the Django app cannot
help; the encode must move off the request path (dedicated chunk service,
precomputed mags, or both). Confirms doc `20`.

### 3.4 Memory

| Volume | RSS after cold open | RSS after scrub | Growth |
|---|---|---|---|
| heart (950 MB file) | 124.9 MB | 1190.7 MB | **+1254 MB** |
| liver (2.8 GB file) | 88.4 MB | 2837.4 MB | **+1129 MB** |

With `MAX_OPEN_VOLUMES = 8`, a worker touching 8 large volumes maps them all.
RSS growth is unbounded relative to the LRU's nominal slice count (cache held
only 45 slices at 1.3 GB RSS). No soak target exists yet; **`21`'s "+15 % over
2 h" cannot be evaluated until an eviction policy exists.**

---

## 4. Paint / save latency — the label write path

Harness: `bench_paint.py`, calling `services.set_label_slice_ids()` (every
brush-stroke commit and every explicit Save) and `get_label_slice_ids()` (the
read issued on landing on a slice). Runs against a scratch `MITO_DATA_ROOT`
with the real image symlinked in — the source volumes were never written to,
and the temporary working-label files were removed afterwards.

| Measurement | heart (6.9 MPix) | **liver (17.7 MPix)** |
|---|---|---|
| Working-label file created | 1.9 GB | **5.65 GB** |
| Cold first paint (fresh worker) | 125.5 ms | 346.9 ms |
| **Warm paint p50 (Z)** | 103.6 ms | **284.9 ms** |
| **Warm paint p95 (Z)** | 119.4 ms | **308.0 ms** |
| Label slice read p50 (Z) | 20.2 ms | 66.6 ms |
| Warm paint p50 (Y plane, 0.6 MPix) | 23.4 ms | 32.1 ms |

### The stroke size does not matter

Every measurement above paints **2 % of the plane** — a plausible brush stroke —
yet cost tracks the *whole plane*: ≈ **16 ms per megapixel written**, ≈ 4 ms/MPix
read, the same constant across both volumes and all axes (the Y-plane writes are
cheap only because a Y plane is 0.6 MPix, not because less was painted).

The reason is the protocol: the editor RLE-encodes and PUTs the **entire slice**
on every commit, and the server decodes the entire plane, writes it, diffs it
against the previous contents, and re-encodes footprints for lifecycle tracking.
A one-brush-dab edit and a full-plane repaint cost the same.

> **Design consequence:** this is the empirical case for Phase 7's op-log /
> chunk-delta model, and it is a *precondition* for Phase 10 (autosave). Doc
> `21`'s "autosave ack < 2 s p95" is already met for a single stroke (308 ms) —
> but that number is misleading: autosave fires per stroke, and 285 ms of
> full-plane work per dab is not viable at brush cadence, especially given
> §3.3 (concurrent requests to the same app process serialize). Autosave must
> not be built on the current whole-slice PUT.

Secondary findings:

- **A working label copy is a full-size uint16 volume** — 5.65 GB for liver,
  allocated on first paint. Storage planning for Phase 11 must budget this per
  in-progress annotation, not per approved label.
- `label_max_id` is an O(volume) `mm.max()` scan cached **per process**. Cheap
  here on a fresh sparse file, but every gunicorn worker pays it independently
  on its first paint of each volume, and the cache is another piece of
  process-local state that multi-worker deployment breaks (doc `13` §E).

---

## 5. Concurrency safety of task assignment

Harness: `bench_claim_race.py` — the current read-modify-write claim pattern
(mirroring `services.assign_tasks_rule_based`) against a throwaway SQLite DB,
N threads released from a barrier. Never touches `backend/db.sqlite3`.

**`connections['default'].features.has_select_for_update` → `False`.**
`assign_tasks_rule_based:69` calls `.select_for_update()`; on SQLite this is a
**documented silent no-op**. The intended lock does not exist in production.

| Scenario | Successful claims | Distinct tasks | **Double claims** | Lock errors |
|---|---|---|---|---|
| 20 workers, 1 task, 5 trials | 1 per trial | 1 | **0** | **87 / 100** |
| 20 workers, 1 task, WAL + 30 s busy timeout | 1 per trial | 1 | **0** | **95 / 100** |
| 20 workers, 20 tasks, 3 trials | 1–2 per trial | 1–2 | **0** | 56 / 60 |
| **8 workers, 50 tasks, 3 trials** | **1 per trial** | 1 | **0** | **21 / 24** |

### 5.1 After the PostgreSQL migration (2026-07-27)

Same harness, same scenarios, now against PostgreSQL 16.14 with
`select_for_update(skip_locked=True)`:

| Scenario | SQLite claims | SQLite lock errors | **PG claims** | **PG lock errors** |
|---|---|---|---|---|
| 20 workers, 1 task | 1 | **87–95 %** | 1 | **0** |
| 20 workers, 20 tasks | 1–2 | 18–19 / 20 | **20** | **0** |
| 8 workers, 50 tasks | 1 | 7 / 8 | **8** | **0** |

**Claim success went from 5–12 % to 100 %**, with zero double claims. The
failure mode the baseline documented is gone, and the capability that fixes it
is now real: `has_select_for_update`, `..._skip_locked`, and `..._nowait` all
report `True` (they were `False` on SQLite).

`skip_locked` is what converts contention into throughput — a worker whose
first-choice row is held moves to the next instead of queueing. This is the
primitive Phase 3's `claim_next_task` will be built on.

Covered by `annotation/test_concurrency.py`, which skips itself off PostgreSQL
rather than asserting something the engine cannot honour.

### Correction to the research pack

Doc `14` rates concurrency **"Critical — unsafe multi-worker"**, and doc `05`
implies the risk is double-claiming. **The measured failure mode is different,
and worth stating precisely:**

- **Data integrity is not violated.** No double claim in any configuration.
  SQLite's global write lock serializes writers, which accidentally provides
  the mutual exclusion `select_for_update()` was supposed to.
- **Availability collapses instead.** 87–95 % of concurrent claims die with
  `OperationalError: database is locked`. With 8 workers and 50 free tasks —
  no contention on the *work*, only on the *database* — **7 of 8 claims fail.**
- **WAL does not help** (95 lock errors vs 87). This matters because WAL +
  longer timeouts is the reflexive fix; it makes the symptom *worse* here, and
  a deferred-transaction read→write upgrade deadlocks regardless of timeout.

The conclusion (**Postgres is required**) is unchanged and is now better
evidenced. But the justification should be stated as **"concurrent assignment
is effectively unavailable today"**, not "it corrupts data" — the latter is not
true and would misdirect Phase 3 acceptance tests. The Phase 3 race test in the
master prompt ("20 workers → exactly one winner") **already passes today by
accident**; it must be strengthened to also assert **the other 19 receive a
clean, retryable outcome — not a lock error**, and that N workers with N free
tasks yield N claims.

---

## 6. Safety artifacts (requirement D)

| Item | Status |
|---|---|
| DB backup | `/home/weidf/shenb/mito-backups/phase0-20260727-173650/db.sqlite3` (`sqlite3.backup()`, consistent) |
| WIP tracked diff | `wip-tracked.patch` (318 KB, 61 files) |
| WIP untracked | `wip-untracked.tar.gz` (21 paths) |
| Working tree | **unchanged — still 82 dirty paths, no stash created** |
| WK clone | `/home/weidf/shenb/external-research/webknossos` @ `a24aecc6f` (outside mito repo) |
| Label-store backup | **N/A — dev tree has no registered volumes.** Must be defined before any environment with real annotations is migrated. |
| Prod touched | **No.** No migrations, restarts, Cloudflare, deletions, or pushes. |

---

## 7. Numbers that should drive the architecture decision

1. **34 ms per megapixel encoded** — the single most important constant. Scrub
   p95 is 6.6× over target on liver purely from full-res encode. Mags fix it;
   nothing else does.
2. **Throughput is flat at ~1.3–1.7 slice/s regardless of concurrency.** The
   viewer cannot serve two users on one large volume today.
3. **Concurrent task assignment has a 87–95 % failure rate**, with integrity
   preserved by accident rather than by design.
4. **Paint costs ~16 ms/MPix of *plane*, not of stroke** — 285 ms per dab on
   liver, because the client PUTs the whole slice. Autosave cannot be layered
   on the current protocol.
5. **248 → 290 tests, 9 red from in-flight WIP.** Must be green before Phase 1.
6. **RSS grows ~1.2 GB per large volume touched**, with no eviction story.

### Suggested revisions to the provisional targets in `21`

| Metric | `21` target | Measured | Recommendation |
|---|---|---|---|
| TTFV cold | < 3 s | 0.64 s | Already met; tighten to < 1.5 s |
| p95 scrub | < 100 ms | 659 ms | Keep — reachable at mag ≥ 2; make it **mag-explicit** |
| In-flight cap ≤ 12 | — | 12 threads ⇒ 11 s p95 | Cap is meaningless until encode leaves the app; set **per-mag** |
| Heap +15 % / 2 h | — | not measurable | Defer until eviction exists; add **server RSS** budget too |
| Autosave ack < 2 s p95 | < 2 s | 308 ms (single stroke) | Met per-stroke but **misleading** — re-specify as sustained strokes/s at brush cadence |
