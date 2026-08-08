# Phase 9 — Additional annotation tools (ranked P1)

**Status:** complete, shipped inert behind `FEATURE_ANNOTATION_TOOLS`
**Date:** 2026-07-29
**Depends on:** Phases 7–8 (per phase map)
**Gate (phase map):** **ranked P1 done**
**Design record:** [ADR-007](../adr/ADR-007-annotation-tools-p1.md)

---

## The ranking, and what it required

§E9 is one sentence: *"Implement P1 from `19-target-annotation-design.md`. Keep
EfficientSAM/SAM2. Do not remove good mito tools to mirror WK."* Doc 19's
backlog is therefore the authority, and it lists **exactly three P1 items**.

| P1 item | State before | Delivered |
|---|---|---|
| Flood fill 2D (+ limited 3D) | absent | full implementation |
| Overwrite policies | partial — inside `interpolation/core.py` | promoted to shared infrastructure |
| Deep links (xyz, label, hard-case) | partial — hard-case token only; gap matrix verdict "**Generalize**" | xyz + label links added |

The gate is "ranked P1 done", not "every plausible tool", so **P2 and P3 are
deferred and listed** (§Deferred) rather than quietly absorbed.

## What shipped

| Area | Detail |
|---|---|
| Shared | `tools/overwrite.py`, `tools/common.py` — policies, boxes, axis order, caps, plan/apply shapes |
| Tools | `tools/flood_fill.py`, `tools/deeplinks.py` — pure cores |
| Service | `tools/service.py` — one boundary for every tool |
| Golden | `tools/golden/` — 9 fixtures, manifest, generator |
| Flag | `FEATURE_ANNOTATION_TOOLS`, default **False** |
| Tests | `annotation/test_tools.py` — **66 tests**; smoke → 77 tests / 14 configs |
| Benchmark | `benchmarks/bench_tools.py` |
| **Migrations** | **None.** |

EfficientSAM/SAM2 and split/merge/watershed are **untouched**, per §E9.

## Conflicts resolved

**A — overwrite policies were already built, in the wrong place.** Phase 8 put
them in `interpolation/core.py`. Doc 19 ranks them as a *tool-level* P1, so
leaving them there would force every future tool to import from interpolation —
an inverted dependency. Promoted to `tools/overwrite.py`, with
`interpolation/core.py` importing and re-exporting under the original names, so
no Phase 8 code or test changed. Verified: Phase 8's 69 tests still pass, and
`test_interpolation_still_uses_the_shared_policies` asserts they are the same
objects.

**B — deep links span committed backend and uncommitted frontend.** The
hard-case token mechanism works and its frontend is the repository owner's
**uncommitted WIP**. Phase 9 adds the generalisation the gap matrix asks for as
a backend service and **touches no hard-case file**.

**C — is a UI required?** §E9 says only "implement P1 from doc 19"; the gate is
"ranked P1 done", not "shipped in the editor". Backend only — and stated as a
decision, not an omission. Wiring a fill tool into `AnnotationCanvas.tsx` would
mean editing a file that is currently uncommitted user WIP, which is exactly the
risk the brief says to stop for rather than take.

## Flood fill

4-connectivity in-plane, 6 in 3-D. **Deliberately not 8/26**: diagonal
connectivity leaks through single-voxel gaps, which is the classic fill-tool
complaint. `diagonal_no_leak` in the golden set pins the choice.

| Behaviour | Decision |
|---|---|
| Seed already the target label | Warns, returns zero voxels. Clicking a region that is already the right colour is reasonable, not an error. |
| Seed out of bounds | Rejected. |
| "Limited 3D" | `MITO_TOOL_MAX_FILL_DEPTH` (32 slices) **on top of** the voxel cap, so a 3-D fill cannot quietly become a whole-volume flood. |
| Overwrite | Honoured; skipped voxels are reported as a warning rather than silently dropped. |
| Determinism | Connected components are a property of the array, not of traversal order. |

### The performance bug the benchmark caught

The first implementation was a Python BFS — one interpreter iteration per voxel.
Measured: **694 ms at 256², 4.9 s at 512²**. Unusable for a tool a user clicks.

Replaced with `scipy.ndimage.label` (already a declared BSD-3 dependency), which
computes the same connected component in C:

| Plane | BFS | vectorised | speedup |
|---|---|---|---|
| 64² | 43.9 ms | **0.32 ms** | 137× |
| 256² | 693.8 ms | **1.04 ms** | **667×** |
| 512² | 4 918.6 ms | **3.16 ms** | **1 556×** |
| 1024² | — | **11.64 ms** | — |

3-D depth 32: 6 638 ms → **8.22 ms** (807×).

**The golden fixtures made this safe.** All nine passed unchanged after the
algorithm was swapped, which is precisely what implementation-independent
expectations are for: the mathematics was pinned, so the optimisation could be
verified rather than trusted.

### Bounded vs full-plane

2048² plane containing one small walled room:

| Strategy | Region | p50 | Peak memory | Voxels |
|---|---|---|---|---|
| full plane | 2048 × 2048 | 57.24 ms | 28.7 MB | 3 481 |
| **bounded bbox** | 61 × 61 | **0.32 ms** | **43.9 KB** | 3 481 |

**179× faster, 653× less memory, identical output.** The benchmark asserts both
and exits non-zero on either, so a regression to whole-plane processing fails
rather than being published.

Pathological input — a 256² plane of 16 384 isolated single voxels — completes
in 0.77 ms and fills exactly one voxel.

## Overwrite policies

`overwrite_empty` (default, existing segments win) and `overwrite_all`.
`writable_mask()` **returns** the permitted subset rather than applying it, so a
plan can count and preview affected voxels without mutating anything — which is
what makes the plan step honest.

The conservative default is deliberate: silently destroying an existing segment
is the worse failure.

## Deep links

```
mito://volume/<id>?z=&y=&x=&label=&task=
mito://hard-case/<token>
```

Deterministic encoding (fixed parameter order — links get bookmarked, compared
and diffed). Strict parsing: unknown scheme, non-integer coordinate, negative
value, malformed token, or **partial position** are rejected rather than
partially applied. A half-applied position would send the viewer somewhere
plausible but wrong, with no signal.

**Parsing yields a descriptor, never an authorisation.** Resolution re-checks
permissions server-side, so a link cannot grant access its holder lacks;
`test_parsing_grants_no_authority` asserts the descriptor carries no user, role
or permission field. Hard-case links delegate to the existing token mechanism,
unchanged.

## Phase 7 integration

Identical contract to Phase 8, deliberately — a caller that integrated one tool
has integrated all of them. One operation per apply, metadata-only payload
(< 2 KB measured, no voxels), `expected_version` honoured, idempotent replay
returns the original **without re-applying**, a reused key with different
parameters is rejected, locked tasks refused, and a write failure rolls the
operation back.

Concurrency on real PostgreSQL connections: 4 concurrent applies → dense
sequence 1–4; 4 concurrent replays of one key → exactly one operation; two users
applying simultaneously → version 4, no errors.

**Undo does not restore voxels.** Phase 7 deferred the snapshot store and
Phase 9 does not add one — the P1 list does not include it, and inventing a
recovery mechanism here would pre-empt Phase 10's "Autosave + recovery" (P0).
Asserted by `test_undo_does_not_restore_voxels`.

## Security limits

| Limit | Value | Enforced |
|---|---|---|
| Voxels per call | 16 M | before allocation |
| 3-D fill depth | 32 slices | before allocation |
| Plane dimension | 8192 | before allocation |
| Deep-link length | 2048 chars | on parse and build |
| Operation payload | Phase 7's 16 KiB | on append |

Adversarial coverage: negative coordinates, out-of-range seeds, float dtype,
reserved label 0, label overflow for the dtype, empty and malformed boxes,
oversized regions, 16 k-component inputs, malformed links, replay storms, and
concurrent conflicting applies.

## Deferred (below the gate)

| Item | Priority | Reason |
|---|---|---|
| Contour/trace tool | P2 | Doc 19 itself hedges: "If fits UI". |
| Brush presets / shortcuts | P2 | Pure UI, no backend component. |
| Segment metadata panel | P2 | Needs a UI surface. |
| Label locking | P2 | Overlaps Phase 5's `annotation_locked`; needs its own design. |
| Agglomerate proofreading | P3 | Doc 19: "Optional; mito split/merge may suffice". |
| Frontend for the P1 tools | — | Conflict C. |

## Known limits

- **No HTTP API and no UI** — backend services only, per conflict C.
- **Undo does not restore voxels** — Phase 10.
- **Flood fill is bounded, not streaming**; regions beyond the caps must be
  split by the caller.
- Deep-link *resolution* (turning a descriptor into a permitted view) is left to
  the caller; Phase 9 provides encode/parse and the explicit guarantee that
  parsing grants nothing.
