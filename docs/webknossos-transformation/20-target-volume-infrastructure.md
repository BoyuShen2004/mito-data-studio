# 20 — Target Volume Infrastructure

## Status quo problems

| Issue | Evidence |
|---|---|
| No pyramid | `slice_io` reads full-res plane |
| Django hot path | `VolumeSliceView` in app process |
| Cache locality process-bound | `MAX_OPEN_VOLUMES=8` memmap LRU |
| Format enum lies | zarr/hdf5/n5 listed but not opened |
| Whole-volume tools | track/watershed/mesh load arrays |

## Target topology

```
                 ┌─────────────┐
   Browser ─────►│ Django API  │  authz, tasks, metadata, chunk tokens
                 └──────┬──────┘
                        │ JWT/chunk token
                 ┌──────▼──────┐
   Browser ─────►│ Chunk Svc   │  async read, cache, compress, metrics
                 └──────┬──────┘
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
   Zarr3 raw      Zarr3 pred     Annotation deltas
   (immutable)    (immutable)    (mutable store)
```

## Format plan

1. **Interactive derivative:** Zarr3 (or Zarr2 if needed) with mags `1,2,4,8…` (anisotropic-aware).
2. **Source of truth archive:** keep TIFF/NIfTI; conversion is additive.
3. **Annotations:** sparse chunk store (Zarr group or custom) + op log — not full volume rewrite.
4. **Libraries:** prefer TensorStore/zarr-python; use AGPL `webknossos.Dataset` only with compliance.

## Pyramid job

`ProcessingJob(type=build_pyramid)` → Slurm/local → write derivative → validate random chunk checksums → mark Volume `ready_streaming=true`.

## Authz

Chunk service validates signed token: `user, volume_id, layers[], exp, read|write`. Django remains source of ACL truth.

## Acceptance

- Scrubbing uses mag≥2 until settle, then refine.
- Django CPU not proportional to chunk QPS.
- Two workers never serve incoherent annotation writes.
