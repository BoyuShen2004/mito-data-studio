# Phase 14 — scope note

**Official title:** Rendering/nav redesign
**Dependency:** Phase 13 frontend chunk cache/scheduler
**Gate:** UI familiarity check

Phase 14 mounts the Phase 13 chunk adapter into the real familiar Canvas2D
viewer. It adds pixel-correct XY/XZ/YZ assembly, stable raw-dtype intensity
mapping, coarse-to-fine replacement, explicit TIFF fallback, browser
correctness/performance tests and memory evidence.

Supported input dtypes are the Phase 12 little-endian integer and floating
types. The stable default window is `VolumeMeta.display_range`; brightness and
contrast remain the existing post-mapping controls. Navigation continues to
support all three axes, slider/keyboard stepping, zoom, fit modes and pan.

`VITE_FEATURE_CHUNK_RENDERER` defaults to false. The disabled path retains the
existing TIFF/PNG behavior. The enabled path requires authenticated Phase 12
capabilities; public shares and any failed/corrupt/unauthorized chunk session
fall back visibly and one-way to TIFF.

The completion evidence combines the official familiarity gate with doc 21:
warm p95 below 100 ms, first useful frame below 1 s warm / 3 s cold on the same
LAN, in-flight cap at most 12 and soak growth within 15% after warm-up.

Excluded: WebGL rewrite, mesh/large-label work (Phase 15), triple-view UI,
persistent cache, production migration, deployment, source/label write-path
changes and any phase after 14.
