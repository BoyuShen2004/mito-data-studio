# 06 — WEBKNOSSOS Annotation Analysis

## Tooling inventory (**DOC**: volume tools; **CODE**: frontend volumetracing)

| Tool | Behavior notes |
|---|---|
| Trace | Contours; continuous stroke can fill interior |
| Brush | Paintable; Shift+wheel size; overwrite modes |
| Eraser | Trace/brush eraser variants |
| Fill | 2D/3D flood; optional bbox restrict; local 3D bbox for perf |
| Quick Select | Threshold mode always; AI mode if deployment supports |
| Interpolation | Active segment between last labeled & current slice (V); task setting gated |
| Proofreading | Agglomerate/supervoxel graph merge/split (**DOC** proofreading; TracingStore graphs) |
| Segments UI | Rename/organize segment IDs; active ID in status bar |
| Skeleton / Flight | Separate major mode (connectomics heritage) |
| Meshes | Ad-hoc + precomputed mesh visualization |
| Undo/Redo | Volume bucket operation history (sagas/tests for bucket eviction with saving) |
| Autosave | PushQueue of dirty buckets to TracingStore (**CODE**: `pushqueue.ts`) |

## Persistence architecture (**CODE**/DOC)

- Immutable dataset layers live in **Datastore**.
- Mutable volume annotations live as **bucketed tracings** in **TracingStore** (FossilDB), not by rewriting full volumes.
- Frontend maintains `DataCube` buckets, `PullQueue` (read), `PushQueue` (write).
- Mapping lock / agglomerate tools for proofreading large segmentations.

## Collaboration & sharing (**DOC**)

- Dataset/annotation URLs with coordinates and viewer state.
- Visibility + team permissions.
- Quick share patterns for publications and collaborators.

## Comparison seeds for mito

mito’s Canvas2D editor already has brush/erase/box erase/EfficientSAM/SAM2 track/split/merge — strong domain tooling. Missing relative to WK maturity: interpolation, continuous autosave of sparse ops, bucketed undo, deep links with full viewer state, flood-fill modes, overwrite policies, production-grade mesh pipeline.
