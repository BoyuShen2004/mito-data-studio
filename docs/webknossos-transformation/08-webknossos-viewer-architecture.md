# 08 — WEBKNOSSOS Viewer Architecture

## Stack (**CODE**: `CLAUDE.md`, `package.json`)

- TypeScript + React + Ant Design
- Redux + Redux-Saga
- Three.js (3D / meshes)
- WebGL plane rendering via texture bucket managers
- Web Workers (`async_bucket_picker.worker.ts`, Comlink)
- Vitest unit/e2e/screenshot suites

## Core modules (`frontend/javascripts/viewer/model/`)

| Module | Role |
|---|---|
| `bucket_data_handling/data_cube.ts` | Bucket address space, cache, dirty state |
| `pullqueue.ts` | Priority queue, batch size 6, abortable fetches, retries |
| `pushqueue.ts` | Persist dirty annotation buckets |
| `texture_bucket_manager.ts` | GPU texture residency |
| `layer_rendering_manager.ts` | Multi-layer compose |
| `prefetch_strategy_plane.ts` / `arbitrary` | View-dependent prefetch |
| `loading_strategy_logic.ts` | BEST_QUALITY_FIRST vs PROGRESSIVE_QUALITY |
| `wkstore_adapter.ts` | Datastore HTTP |
| volumetracing sagas | Tools, interpolation, proofreading sync |

## Orthographic + 3D model

- XY / XZ / YZ planes + 3D viewport; shared flycam coordinates; crosshairs.
- Multi image + segmentation layers; opacity; mappings for large IDs.
- Flight mode for skeleton (historical strength).

## Why navigation stays responsive (**CODE** evidence)

1. **Chunk/buckets**, not full volumes.
2. **Priority PullQueue** with highest priority never dropped; stale work abortable.
3. **Batching** (BATCH_SIZE=6, BATCH_LIMIT concurrent batches).
4. **Prefetch** around current planes.
5. **Progressive / quality strategies**.
6. **Texture reuse** and bucket eviction with save coordination (tests: `bucket_eviction_with_saving.spec.ts`).
7. Separate **datastore** process so app servers are not blocked on disk IO.
8. Annotation writes are **sparse bucket pushes**, not whole-volume rewrites.

## Contrast to mito

mito: Canvas2D + `<img>` JPEG slices from Django memmap TIFF; client LRU ~16 images; no WebGL planes; no bucket priority abort; whole-volume ops for track/watershed/mesh.
