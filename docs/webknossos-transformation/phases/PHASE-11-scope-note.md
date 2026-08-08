# Phase 11 — scope note

**Official title (phase map row 11):** **Volume storage & pyramids**
**Depends on:** Phase **0** — *not* Phase 10.
**Gate (phase map):** **format decision locked**
**Design record:** [ADR-009](../adr/ADR-009-volume-storage-and-pyramids.md)

---

## 1. Authoritative sources, read in full

| Source | What it fixes |
|---|---|
| `27-claude-code-phase-map.md` row 11 | `Volume storage & pyramids \| depends 0 \| gate: format decision locked` |
| `CLAUDE_CODE_MASTER_PROMPT.md` §E11–E13 | *"Follow `20` + `21` docs: Pyramid Zarr3 derivatives (Phase 11). Chunk service with token auth (Phase 12). Frontend PullQueue-like manager (Phase 13)."* |
| `20-target-volume-infrastructure.md` | The substance: status-quo problems, target topology, **format plan**, pyramid job, authz, acceptance |
| `21-target-rendering-architecture.md` | Performance targets — but see §5, they gate Phase 13 |
| `14-complete-feature-gap-matrix.md` rows 29–30 | *Formats*: WK has Zarr/WKW/N5/NG, mito has TIFF/NIfTI memmap → **Critical**, verdict "Pyramids + chunk svc". *Chunk streaming* → Phase 12/13. |
| ADR-001 … ADR-008 | Control/data-plane split, no voxels in PostgreSQL, data-root ownership |
| Existing code | `annotation/visualization/slice_io.py`, `volumes/`, `processing/` (jobs, adapters, registry), `core/choices.py` |

**Phase 11 is not inferred from its title or from Phase 10's leftovers.** The
gate is a *decision* gate, and the substance is doc 20's format plan.

## 2. The gate, read precisely

> **format decision locked**

This is not a soak gate and not a latency gate. It is satisfied when the
derivative format is chosen, written down with its rejected alternatives, and
**locked by working code that produces and validates that format** — a decision
nobody can quietly reinterpret later because there is a reader, a writer and a
checksum test pinning it.

The master prompt's *"Acceptance scrubbing: p95 slice change after warmup"* is
stated for **E11–E13 collectively**, and the phase map assigns `p95 scrub target`
to **row 13**, not row 11. Phase 11 therefore does **not** have a p95 gate: it
has no chunk service and no frontend to scrub with. Benchmarks here measure
*pyramid build and read*, which is what this phase actually owns.

## 3. Required functionality

From doc 20 §Format plan and §Pyramid job:

1. **Interactive derivative** in Zarr v3, mags `1,2,4,8…`, **anisotropy-aware**
   (a 40 nm z / 4 nm xy volume must not be downsampled in z at the same rate).
2. **Source of truth stays TIFF/NIfTI** — conversion is *additive*. Nothing
   existing is rewritten, moved or deleted.
3. **Pyramid job**: `ProcessingJob(type=build_pyramid)` → local/Slurm → write
   derivative → **validate random chunk checksums** → mark the volume
   `ready_streaming=true`.
4. **Library choice** recorded: doc 20 says *"prefer TensorStore/zarr-python; use
   AGPL `webknossos.Dataset` only with compliance."*

## 4. Explicit exclusions

| Excluded | Owner |
|---|---|
| Chunk/datastore service, signed chunk tokens | **Phase 12** (row 12, gate `authz + metrics`) |
| Frontend PullQueue / chunk cache / scheduler | **Phase 13** (row 13, gate `p95 scrub target`) |
| Rendering or navigation redesign | Phase 14 |
| Meshes, large-label scale | Phase 15 |
| Switching the editor's read path onto the pyramid | Phase 13–14. Phase 11 builds the derivative; nothing reads it yet. |
| Annotation sparse chunk store | Doc 20 §Format plan item 3 describes it, but annotations are Phase 7/10's op log + working mask; converting them is not row 11. |
| Rewriting `slice_io` | Out of scope — additive only. |

Doc 20 also describes the chunk service's authz and the target topology. Those
paragraphs are **context for the stack**, not Phase 11 deliverables; row 12 owns
them and its gate (`authz + metrics`) is where they are graded.

## 5. Dependencies on earlier phases

Row 11 depends on **Phase 0** only. It does *not* depend on 7–10, and must not
require them: a deployment running none of the annotation flags must still be
able to build a pyramid. Concretely it reuses:

- `processing` app — `ProcessingJob`, adapters (`local`, `slurm`), registry.
- `volumes` — `Volume`, `image_location`, voxel size.
- `core.data_root` — every write goes through `assert_owned` (Phase-10-era
  guardrail, applies unchanged).
- `annotation/visualization/slice_io` — **read only**, for the source array.

## 6. Backend responsibilities

- Pure core: downsample planning (mag ladder, anisotropy), block iteration.
- Writer: Zarr v3 group + arrays, one array per mag.
- Validator: random-chunk checksum verification against the source.
- Service: job orchestration, flag gate, readiness transition.
- Model: pyramid metadata + `ready_streaming` on `Volume`; additive migration.

## 7. Frontend responsibilities

**None.** Nothing in row 11, §E11 or doc 20 gives Phase 11 a UI. The frontend
chunk manager is row 13. Adding UI here would pre-empt it.

## 8. Models and migrations

Additive only, reversible, no backfill of fabricated history:

- `Volume.ready_streaming` (bool, default False)
- `Volume.pyramid_metadata` (JSON, default dict) — mags, chunk shape, dtype,
  axis order, checksum sample, built-at
- `ProcessingJobType.BUILD_PYRAMID` — a new choice, no data change

Legacy volumes are valid unchanged: `ready_streaming=False`, empty metadata.

## 9. APIs

No new public read API — chunk serving is Phase 12. The existing
`ProcessingJob` API surface is the trigger. If a convenience endpoint is added
it must be authenticated, permission-checked and flag-gated, and must not serve
voxels.

## 10. Permissions

Building a derivative is a data-management action: manager/requester with
registration rights over the project, matching `volumes` today. No anonymous
access. Reading pyramid *metadata* follows existing volume view permissions.

## 11. Feature flag

None is named in the roadmap for row 11. Following the ADR-007 / ADR-008
precedent (E9 and E10 named none either), Phase 11 names its own:
**`FEATURE_VOLUME_PYRAMIDS`**, default **False**. Flag off ⇒ no new behaviour,
no job type offered, no writes.

## 12. Concurrency

- Two builds for one volume must not interleave writes — one wins, the other
  reports the conflict.
- A build must be **idempotent**: re-running produces the same derivative and
  does not duplicate work or corrupt a partial result.
- Partial output must never be marked ready: readiness flips only after
  validation.

## 13. Performance

Phase 11 owns *build* and *derivative read*, not scrub latency:

- Build must be **streaming/bounded** — no whole-volume array in memory. This is
  the ADR-005/008 rule again: a multi-gigabyte volume cannot be loaded to
  produce its own downsample.
- Reading a mag-`k` plane must be **cheaper than reading mag-0**, and measurably
  so; that is the entire point of the pyramid.
- Numbers recorded under `docs/.../benchmarks/`, per Phase 0's convention.

## 14. Security constraints

- All derivative writes under `MITO_DATA_ROOT`, through
  `core.data_root.assert_owned`.
- Registered **source images and official labels remain read-only** — a
  conversion that mutated its input would be the exact failure the stabilisation
  pass eliminated.
- No dense voxel payloads in PostgreSQL (ADR-005 conflict B): the database holds
  paths, mags, shapes and checksums.
- Bounded request/storage sizes; no unbounded scans.
- **Licensing:** doc 20 permits AGPL `webknossos.Dataset` "only with
  compliance". This phase does not use it — see ADR-009 §Licensing.

## 15. Required tests

Flag off/on; empty and single-slice volumes; anisotropic ladders; boundary
(non-power-of-two shapes, partial chunks); invalid input; auth and permission
denial; cross-project isolation; idempotent re-run; concurrent builds; restart
mid-build; checksum validation catching corruption; readiness only after
validation; migration forward **and reverse**; legacy volumes untouched;
external source byte-identical; writes confined to `MITO_DATA_ROOT`; fresh
database; fresh isolated checkout.

## 16. Completion gate — restated as checkable items

1. The format is **locked in an ADR**: version, mags, chunking, axis order,
   dtype, compression, placement, and the rejected alternatives with reasons.
2. A **writer** produces that format and a **reader** reads it back exactly.
3. **Random chunk checksums** validate a built derivative, and a corrupted chunk
   is detected.
4. `ready_streaming` flips only after successful validation.
5. Build is bounded in memory and idempotent.
6. Flag off ⇒ byte-identical existing behaviour.
7. Source images and labels are byte-identical after a build.
8. Benchmarks recorded for build and mag-read.

## 17. Relationship to Phase 12

Phase 12 (`Chunk/datastore service`, gate `authz + metrics`) serves the
derivative this phase writes, behind signed tokens. Phase 11 must therefore
leave a **stable on-disk contract** — the ADR — and must not embed serving,
tokens or metrics. Phase 12 is not started here.
