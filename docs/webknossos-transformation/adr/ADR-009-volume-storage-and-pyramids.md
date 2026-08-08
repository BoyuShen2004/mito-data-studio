# ADR-009 — Phase 11: volume storage and pyramids

**Status:** accepted, 2026-07-30
**Phase:** 11 (volume storage & pyramids)
**Depends on:** Phase 0 (per phase map)
**Gate:** **format decision locked** — this document *is* the gate, and the
writer, reader and checksum tests are what lock it.
**Scope note:** [PHASE-11-scope-note.md](../phases/PHASE-11-scope-note.md)
**Amended by:** [ADR-009-A1](ADR-009-A1-region-mask-layer.md) — the region mask
gets the same derivative as a sibling group per layer (2026-08-04)
**Related:** ADR-001 (control/data-plane split), ADR-005 (no voxels in
PostgreSQL), ADR-008 (data-root ownership)

---

## 1. What is being decided

Doc 20 §Format plan asks for an "interactive derivative: Zarr3 (or Zarr2 if
needed) with mags `1,2,4,8…` (anisotropic-aware)", keeping TIFF/NIfTI as the
source of truth, with conversion additive.

A gate that says "format decision locked" is satisfied by a decision that is
**written down with its alternatives and pinned by code** — a reader, a writer
and a checksum test — so that it cannot be quietly reinterpreted six phases
later when Phase 12 starts serving it.

**The locked format is specified in §3. Everything in §3 is a contract Phase 12
may rely on.**

## 2. Library choice

**Decision: `zarr-python >= 3.1, < 4`, imported lazily.**

| Candidate | Verdict |
|---|---|
| **zarr-python** | **Chosen.** Doc 20 names it as preferred. MIT, as are its dependencies `numcodecs`, `donfig`. Writes the Zarr v3 spec directly. |
| TensorStore | Also named as preferred, and excellent, but a large C++ dependency for a build-side job that is not latency-critical in this phase. Reconsider in Phase 12, where read latency *is* the gate. |
| `webknossos.Dataset` | **Rejected on licensing.** Doc 20 permits it "only with compliance"; it is AGPL, and this codebase has no AGPL component. `THIRD_PARTY_NOTICES.md` needs no new entry as a result. |
| Hand-rolled writer | Rejected. The point of choosing a standard format is interoperability; a bespoke writer would re-earn every bug the spec already fixed. |

`zarr==3.0.6` is **yanked upstream** (`zarr.load()` deletes data). The floor is
therefore `>=3.1`, and the ceiling `<4` because a major bump may change the
on-disk contract this ADR pins.

**The import is lazy.** Nothing imports zarr at module scope, so an environment
with the feature explicitly disabled starts, serves and tests without zarr.
The release environment includes `zarr>=3.1,<4`.

## 3. The locked format

```
<MITO_DATA_ROOT>/<project>/<dataset>/pyramids/<image-stem>.zarr/
    zarr.json                     group metadata, zarr_format: 3
    1/  zarr.json + chunks        full resolution
    2/  …                         xy downsampled 2×
    4/  …
    8/  …
```

| Property | Value | Why |
|---|---|---|
| Spec | **Zarr v3** (`zarr_format: 3`) | Doc 20's first choice; v2 is the fallback it allows, not needed |
| Layout | One **group per volume**, one **array per mag** | Mirrors WK's mag directories; Phase 12 can serve an array without parsing siblings |
| Array name | The **xy downsample factor** as a decimal string: `"1"`, `"2"`, `"4"`, `"8"` | Literally doc 20's "mags 1,2,4,8…" |
| Axis order | **`(z, y, x)`** | The convention every module in this codebase already uses (`tools/common.py`, `label_paths`, `slice_io`). One order, no translation layer |
| dtype | **Preserved from the source** | A derivative that changed dtype would not be a derivative |
| Chunks | **`(1, 512, 512)`**, clipped to the array shape | See §4 |
| Compression | zstd (zarr v3 default codec pipeline) | Label and EM data are highly compressible; zstd decompresses fast enough for a read path |
| Placement | Beside the volume, under the dataset folder | Same rule as masks, metadata and embeddings (`label_paths`), so browsing `data/` matches what the app shows |
| Source | **Never modified.** TIFF stays the source of truth | Doc 20: "conversion is additive" |

Per-array attributes (`zarr.json` → `attributes`):

```json
{"level": 1, "factors": [1, 2, 2], "voxel_size": [40.0, 8.0, 8.0]}
```

`factors` is `(fz, fy, fx)` — the per-axis downsample relative to full
resolution, which is what makes the ladder anisotropy-aware while the array
*name* stays the scalar xy mag doc 20 asks for.

Group attributes record the ladder, the source path, the dtype, the build time
and the checksum sample, so a derivative is self-describing without the database.

## 4. Chunk shape — `(1, 512, 512)`

Slice-oriented, not cubic. The read pattern this whole stack is being built for
is stated in doc 21: *"p95 slice step while scrubbing < 100 ms after warmup"*.
Scrubbing walks z, reading one full plane at a time. A cubic chunk
(e.g. `64³`) would force every plane read to fetch 63 neighbouring planes it
will not use — 64× the bytes for the access pattern that matters.

The cost is that a z-oriented 3-D read touches more chunks. That is accepted:
Phase 11 exists to make *interactive slice viewing* fast, and whole-volume
operations already read the source TIFF, not the derivative.

512 rather than 256: a 512² uint16 tile is 512 KiB uncompressed, comfortably
inside one HTTP response in Phase 12, and quarters the per-chunk overhead versus
256 for the same plane.

## 5. Anisotropy-aware mag ladder

Downsampling z at the same rate as x/y on a 40 nm × 8 nm × 8 nm volume destroys
z resolution five times faster than it should. So the ladder doubles **only the
axes whose physical voxel extent is currently the smallest**, driving the voxel
toward isotropy:

```
factors ← (1, 1, 1)
repeat:
    extent_a ← voxel_size_a × factors_a      for each axis
    double factors_a for every axis whose extent_a ≤ min(extent) × ANISO_TOLERANCE
```

With `voxel_size = (40, 8, 8)` this yields `(1,1,1) → (1,2,2) → (1,4,4) →
(2,8,8)`: z is left alone until xy has caught up, then all three double
together. With isotropic voxels it degenerates to `(1,1,1) → (2,2,2) → …`, the
obvious behaviour.

The ladder stops when a further level would make any axis smaller than
`MIN_MAG_EXTENT` — a pyramid level of 3 voxels is not useful and costs a
directory.

## 6. Where business logic lives

```
pyramid/ladder.py     pure: mag ladder, factor maths, chunk planning
pyramid/downsample.py pure: block reduction, one level from the level above
pyramid/store.py      zarr group/array creation, placement, attributes
pyramid/validate.py   random-chunk checksums against the source
pyramid/service.py    orchestration, flag gate, readiness transition
```

No logic in views, serializers, signals, `save()`, admin actions or React
effects, per the standing rule. `ladder.py` and `downsample.py` import neither
Django nor zarr, so the maths is testable on its own — the same split that let
Phase 8's interpolation core and Phase 9's tool cores be verified against golden
expectations.

## 7. Building: streaming, bounded, idempotent

**Bounded.** Level *n+1* is produced from level *n* in **z-slabs**, never by
loading a volume. A slab is `factors_z` planes of the parent — the minimum
needed to produce one plane of the child. Peak memory is therefore a function of
plane size and the z factor, not of volume size. This is the same rule ADR-006
and ADR-007 established after Phase 8 measured 423 ms dense vs 2.13 ms bounded on
a *single* plane; a multi-gigabyte volume cannot be loaded to downsample itself.

**Idempotent.** A rebuild writes the same bytes for the same input. The
derivative is written to a temporary sibling directory and moved into place only
after validation, so a crashed build leaves no half-pyramid that looks finished,
and a re-run after a crash simply starts again.

**Concurrency.** The readiness transition takes a row lock on the volume;
whichever build validates first flips `ready_streaming`, and a second concurrent
build is refused with a conflict rather than interleaving writes into one store.

## 8. Validation — random chunk checksums

Doc 20: *"validate random chunk checksums"*. Concretely, after writing:

1. Sample *k* chunk coordinates deterministically from a seed recorded in the
   metadata (so a failure is reproducible).
2. For each, read the chunk back from the derivative and recompute the expected
   content from the **source** by applying the same reduction.
3. Compare SHA-256 digests.

Readiness flips **only** if every sampled chunk matches. A derivative that fails
validation is left un-promoted with its failure recorded — never silently marked
ready, and never deleted automatically, because a bad derivative is evidence.

## 9. Data model

Additive, reversible, no fabricated history:

- `Volume.ready_streaming: bool = False`
- `Volume.pyramid_metadata: JSON = {}`
- `ProcessingJobType.BUILD_PYRAMID` — a new choice only

Legacy volumes are valid untouched: not ready, empty metadata. There is no
backfill, because a pyramid that was never built is not a missing record — it is
an accurate absence.

**No voxels in PostgreSQL** (ADR-005 conflict B): the database stores the
derivative's path, mags, shapes, dtype and checksum sample. Bytes live under
`MITO_DATA_ROOT`, and every write passes `core.data_root.assert_owned`.

## 10. Flag, rollout, rollback

`FEATURE_VOLUME_PYRAMIDS` remains false in legacy profiles. As of 2026-08-04 it
defaults **True** in `production_integrated_v1`; new registrations enqueue a
build and managers can Build/Rebuild from the volume page. An explicit false
override still refuses the job type and writes no store.

Rollback is deleting the derivative directory and clearing the two columns; the
source is untouched, so nothing is lost. That is the whole point of "conversion
is additive".

## 11. Observability

Build emits: level count, per-level shape and chunk count, bytes written,
wall time per level, peak resident slab size, chunks validated and the seed. All
recorded on the `ProcessingJob` and in the group attributes, so a derivative can
be audited without re-reading it.

## 12. Performance targets for *this* phase

Row 13 owns `p95 scrub target`; row 11 owns build and derivative read:

| Metric | Target |
|---|---|
| Peak build memory | bounded by slab, not volume size |
| Mag-*k* plane read | materially cheaper than mag-1, measured |
| Rebuild | deterministic and byte-identical |

## 13. Test strategy

Pure cores against hand-computed expectations (ladder, reduction); store
round-trips; checksum validation including a *deliberately corrupted* chunk;
flag off/on; permissions; idempotent re-run; concurrent builds; crash mid-build
leaving no promoted store; migration forward and reverse; legacy volumes
unchanged; source byte-identical; writes confined to the data root; and the
smoke matrix extended with the new flag.

## 14. Licensing

zarr-python, numcodecs and donfig are MIT. No AGPL component is introduced, so
doc 20's "only with compliance" caveat about `webknossos.Dataset` does not
apply — it is not used. `THIRD_PARTY_NOTICES.md` gains the three MIT entries.

## 15. Explicitly not in this phase

Chunk service, signed chunk tokens and serving metrics (Phase 12); frontend
PullQueue, chunk cache and scheduler (Phase 13); switching the editor's read
path onto the derivative (Phase 13–14); annotation sparse chunk store; any
change to `slice_io`.
