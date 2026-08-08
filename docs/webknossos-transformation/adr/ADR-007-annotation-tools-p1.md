# ADR-007 — Phase 9: the ranked P1 annotation tools

**Status:** accepted, 2026-07-29
**Phase:** 9 (additional annotation tools)
**Depends on:** Phases 7–8 (per phase map)
**Gate (phase map):** **ranked P1 done**
**Related:** ADR-001 … ADR-006

---

## 1. The authoritative ranking

§E9 is one sentence: *"Implement P1 from `19-target-annotation-design.md`. Keep
EfficientSAM/SAM2. Do not remove good mito tools to mirror WK."*

Doc 19's backlog is therefore the ranking, verbatim:

| Priority | Feature | Notes |
|---|---|---|
| P0 | Interpolation (SDF) | **done — Phase 8** |
| P0 | Autosave + recovery | Phase 10 |
| P0 | Op-log undo/redo | **done — Phase 7** |
| **P1** | **Flood fill 2D (+ limited 3D)** | "Classical, not only SAM" |
| **P1** | **Overwrite policies** | "empty-only vs everything" |
| **P1** | **Deep links (xyz, label, hard-case)** | WK sharing + mito HardCase |
| P2 | Contour/trace tool | deferred |
| P2 | Brush presets / shortcuts | deferred |
| P2 | Segment metadata panel | deferred |
| P2 | Label locking | deferred |
| P3 | Agglomerate proofreading | deferred |
| — | EfficientSAM/SAM2 | **retain, untouched** |
| — | Split/Merge/Watershed | **retain, untouched** |

**Exactly three P1 items.** The gate is "ranked P1 done", not "implement every
plausible tool", so P2 and P3 are deferred and listed rather than quietly
absorbed.

### Current state of each

| P1 item | State before Phase 9 | Work required |
|---|---|---|
| Flood fill | **absent** | full implementation |
| Overwrite policies | **partial** — implemented inside `interpolation/core.py` in Phase 8, usable only by interpolation | promote to shared infrastructure |
| Deep links | **partial** — hard-case token sharing exists; gap matrix verdict is "**Generalize**" (mito: "Hard-case token", target: "Coord+state URLs") | add xyz + label deep links |

## 2. Scope table

| | Flood fill 2D/3D | Overwrite policies | Deep links |
|---|---|---|---|
| **Source** | doc 19 P1 | doc 19 P1 | doc 19 P1 + gap matrix row 28 |
| **User action** | click a seed voxel, fill the connected region | choose whether a tool may overwrite existing labels | copy/open a link to a position, label, or hard case |
| **Backend** | pure core + service + one operation | shared policy module | encode/parse/resolve service |
| **Frontend** | **none** — see §7 | none | none |
| **Phase 7** | one operation per apply | recorded in every tool's payload | none (read-only, no mutation) |
| **Phase 8 dependency** | none | interpolation re-uses the promoted module | none |
| **Persistence** | none (writes labels through the existing path) | none | none |
| **Flag** | `FEATURE_ANNOTATION_TOOLS` | same | same |
| **Migrations** | none | none | none |

## 3. Conflicts found, and how they were resolved

### Conflict A — "overwrite policies" was already built, in the wrong place

Phase 8 implemented `OVERWRITE_ALL` / `OVERWRITE_EMPTY` inside
`interpolation/core.py`. Doc 19 ranks overwrite policies as a **tool-level P1**,
not an interpolation detail, so leaving them there would mean every future tool
either re-implements them or imports from interpolation — an inverted
dependency.

**Resolution.** The canonical definitions move to `annotation/tools/overwrite.py`
and `interpolation/core.py` imports them, keeping its existing module-level
names as re-exports. Nothing that referenced `core.OVERWRITE_EMPTY` breaks, no
Phase 8 test changes, and the policy now has one home. Least destructive
interpretation: promote, do not relocate-and-rename.

### Conflict B — "deep links" spans committed backend and uncommitted frontend

Hard-case token sharing already exists and works. Its **frontend is the
repository owner's uncommitted WIP** (`hardCases.ts`, `HardCaseList.tsx`,
`HardCaseDetailPage.tsx`, `HardCasesPage.tsx`, `types/hardCase.ts`), which this
pass must preserve and may not edit.

**Resolution.** Phase 9 implements the **generalisation the gap matrix asks for**
— structured deep links carrying position, label and hard-case references — as a
backend service, and touches no hard-case file. The existing token endpoints are
left exactly as they are.

### Conflict C — is a UI required at all?

Doc 19's P1 rows describe tools a user operates, which implies UI. But §E9 says
only "implement P1 from doc 19", the phase gate is "ranked P1 done" rather than
"shipped in the editor", and Phases 6–8 all delivered backend-only under the
same reading.

**Resolution — backend only, and stated as such.** Two independent reasons:
the roadmap gate does not mention UI, and integrating a fill tool into
`AnnotationCanvas.tsx` would mean editing a file that is **currently
uncommitted user WIP** — the exact risk §8 of the brief says to stop for rather
than take. The backend is complete and callable; wiring is a separate decision
for the file's author.

## 4. Shared infrastructure

Tools are **not** independent one-off endpoints. `annotation/tools/` provides:

| Module | Responsibility |
|---|---|
| `overwrite.py` | the two policies and their application to a slice |
| `common.py` | bounding boxes, axis order, dtype/label validation, size caps, the plan/apply result shapes |
| `flood_fill.py` | pure geometric core |
| `deeplinks.py` | pure encode/parse |
| `service.py` | orchestration, permissions, Phase 7 recording |

Conventions, fixed once for every present and future tool:

- **Axis order** `(z, y, x)`; a 2-D plane is `(row, col) == (y, x)`.
- **Bounding box** as `(z0, y0, x0, z1, y1, x1)`, half-open on the upper bound —
  the numpy slicing convention, so no off-by-one translation layer exists.
- **Label 0 is background** and reserved, as in Phase 8.
- **Payloads are schema-versioned** (`tool_schema_version`), rejected if unknown.
- **Every mutating tool** goes plan → apply, records exactly one Phase 7
  operation, and honours `expected_version` and `idempotency_key`.

## 5. Flood fill

**Algorithm:** iterative scanline-free BFS over 4-connectivity in 2-D and
6-connectivity in 3-D, from a seed voxel, over the region whose label equals the
seed's label. Iterative rather than recursive: a 4096² region would blow the
Python stack, and a fill tool that crashes on a large region is not a fill tool.

| Question | Decision |
|---|---|
| Connectivity | 4 (2-D) / 6 (3-D). Not 8/26 — diagonal connectivity leaks through single-voxel gaps, which is the classic fill-tool complaint. |
| Seed on target label | No-op returning zero voxels, not an error: clicking a region that is already the target colour is a reasonable user action. |
| Seed out of bounds | Rejected. |
| "Limited 3-D" | Bounded by `max_voxels` **and** by an explicit `max_depth` on the z extent, so a 3-D fill cannot silently become a whole-volume flood. |
| Overwrite policy | Honoured — `overwrite_empty` fills only background, `overwrite_all` replaces whatever it reaches. |
| Determinism | The visited set is a boolean array and the frontier a deque; the *result* is order-independent because BFS over a fixed neighbourhood reaches exactly the connected component. Asserted. |
| Memory | One boolean visited array plus a frontier bounded by the region perimeter. |

## 6. Deep links

A deep link is a **structured, signed-free descriptor**, not a URL string built
by concatenation:

```
mito://volume/<volume_id>?z=&y=&x=&label=&task=       # position + label
mito://hard-case/<token>                              # existing share
```

| Question | Decision |
|---|---|
| Encoding | Deterministic query ordering, so the same target always produces the same link — links are compared, bookmarked and diffed. |
| Parsing | Strict. Unknown scheme, missing id, non-integer coordinate, or negative label is rejected with a reason rather than partially applied. |
| Authority | Parsing yields a **descriptor**, never an authorisation. Resolution re-checks permissions server-side, so a link cannot grant access its holder does not have. |
| Hard-case links | Delegated to the existing token mechanism, unchanged. |
| Mutation | **None.** Deep links are read-only and record no operation. |

## 7. Security and abuse limits

| Limit | Value |
|---|---|
| Max voxels per tool call | `MITO_TOOL_MAX_VOXELS`, default 16 M |
| Max 3-D fill depth | `MITO_TOOL_MAX_FILL_DEPTH`, default 32 slices |
| Max plane dimension | 8192 per axis |
| Max operation payload | Phase 7's 16 KiB, unchanged |
| Deep-link string | 2048 characters |

Every limit is checked **before** allocation. A small request cannot trigger an
unbounded scan: the fill is confined to the caller-supplied box, and the box is
capped.

Adversarial inputs — negative coordinates, out-of-range seeds, non-finite
values, reserved labels, oversized boxes, malformed links — are rejected with
machine-readable reasons and are covered by tests.

## 8. Phase 7 integration

Identical contract to Phase 8, deliberately: one operation per apply, bounded
metadata payload, no voxel arrays, `expected_version` honoured, idempotent
replay returns the original, a reused key with different parameters is rejected,
locked annotations refused, and a write failure rolls the operation back.

**Undo does not restore voxels.** Phase 7 deferred the snapshot store and
Phase 9 does not add one — the P1 list does not include it and inventing a
recovery mechanism here would pre-empt Phase 10's "Autosave + recovery" (P0).
Stated plainly and asserted by test rather than implied.

## 9. Rollout

`FEATURE_ANNOTATION_TOOLS`, default **False**. Planning needs only this flag;
applying also needs `FEATURE_ANNOTATION_OPS`, exactly as Phase 8 does, because
applying records an operation.

**No migrations.** No new persistent state.

## 10. Deferred, with reasons

| Item | Priority | Why not now |
|---|---|---|
| Contour/trace tool | P2 | Below the gate. Doc 19 itself hedges: "If fits UI". |
| Brush presets / shortcuts | P2 | Pure UI; no backend component. |
| Segment metadata panel | P2 | Below the gate; needs a UI surface. |
| Label locking | P2 | Below the gate; overlaps Phase 5's `annotation_locked`, so it needs its own design. |
| Agglomerate proofreading | P3 | Doc 19: "Optional; mito split/merge may suffice". |
| Frontend for the P1 tools | — | §3 conflict C. |

EfficientSAM/SAM2 and split/merge/watershed are **retained and untouched**, per
§E9's "do not remove good mito tools".

## 11. Acceptance criteria

1. Flood fill matches independently-derived golden expectations exactly.
2. Fill is confined to the supplied box and never scans a whole volume.
3. Overwrite policies are shared, and interpolation still uses them unchanged.
4. Deep links round-trip deterministically and grant no authority.
5. One apply → exactly one operation, bounded payload, no voxels in PostgreSQL.
6. Flag off → every tool refuses and nothing else changes.
