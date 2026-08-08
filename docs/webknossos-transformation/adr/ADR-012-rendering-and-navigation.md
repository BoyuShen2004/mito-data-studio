# ADR-012 — Phase 14 rendering and navigation

**Status:** accepted, 2026-07-31
**Phase:** 14 (rendering/nav redesign)
**Depends on:** Phase 13
**Official gate:** UI familiarity check

## Decision and source resolution

The phase map supplies the official gate. The master prompt and target
rendering architecture require the familiar Canvas2D proofreading surface to
remain, while the Phase 13 handoff assigns Phase 14 the production data-source
mount, raw dtype conversion, XZ/YZ assembly, progressive replacement, browser
tests and same-LAN/soak evidence. These sources are complementary:

- preserve the current image element, Canvas overlay, tools and write path;
- replace only the source-image read adapter behind
  `VITE_FEATURE_CHUNK_RENDERER=false`;
- use doc 21's quantitative stack targets: warm scrub p95 below 100 ms,
  first useful frame below 1 s warm / 3 s cold on the same LAN, at most 12
  in-flight reads, and heap growth no more than 15% after soak warm-up.

WebGL, mesh work, orthographic triple-view, persistent browser caching and
production rollout are explicitly excluded.

## Data-source selection and fallback

Authenticated viewers use the chunk path only when the Phase 14 flag is true
and Phase 12 capabilities initialize successfully. Public token-backed shares,
missing/building pyramids, unsupported responses, authorization failures,
network errors and corrupt chunks use the established TIFF/PNG endpoint.
Fallback is automatic, user-visible and one-way for the mounted viewer session
to prevent source flapping. It never reuses a partial or stale chunk frame.
Disabling the flag executes the original TIFF code path.

## Coordinate contract

Source voxel `(z,y,x)` is canonical. Plane rows/columns are:

- XY (`axis=z`): row `y`, column `x`, fixed `z`;
- XZ (`axis=y`): row `z`, column `x`, fixed `y`;
- YZ (`axis=x`): row `z`, column `y`, fixed `x`.

Magnification and chunk indices are read coordinates only. Coarse planes are
nearest-neighbor expanded to the exact source-plane dimensions before entering
the existing image/overlay stack, so annotation coordinates never inherit
magnification coordinates. CSS zoom/pan converts to plane coordinates before
the existing `voxelFromSlice` conversion. `devicePixelRatio` changes backing
resolution, never voxel identity. Cropped chunks retain their absolute voxel
offsets. Anisotropic spacing affects display aspect only; it does not change
voxel addressing.

## Rendering pipeline

1. Existing axis/index/zoom/pan state defines the viewport generation.
2. The adapter selects the intersecting chunks and cancels stale generations.
3. Phase 13 PullQueue applies priority, dedupe, concurrency and retry policy.
4. The strict client decodes validated little-endian typed arrays.
5. A pure assembler produces XY, XZ or YZ row-major planes.
6. A pure conversion applies the volume-wide metadata display window.
7. Scrub velocity chooses a coarse level with hysteresis at the adapter seam.
8. Canvas2D creates a pixel-aligned PNG object URL for the existing image.
9. Existing immutable region and editable working-label overlays composite
   above it without changing order, opacity, interpolation or tools.
10. Axis, index, generation and abort state are checked before replacement.
11. A complete fine frame atomically replaces a valid coarse frame.

Chunk calculation, assembly, coordinate conversion and intensity mapping are
pure and independently tested. Browser encoding and React commit are effects.

## Dtype and intensity window

The Phase 12 contract supports uint8/16/32, int8/16/32 and float32/64. The
strict client rejects unknown dtype, byte order, shape or byte length before
rendering. Values map through `VolumeMeta.display_range`, stable across slices
and magnifications. Values clamp to `[0,255]`; NaN and negative infinity map
to black and positive infinity to white. Existing CSS brightness/contrast is
applied after this stable base mapping. Per-slice normalization is forbidden.

## Magnification and coarse-to-fine

Settled navigation requests the finest available level. Rapid sequential
navigation uses the first available numeric magnification at least 2 as the
initial frame and queues finest as `REFINE`; current content remains until a
complete replacement exists. Both frames are expanded to the same source
plane dimensions, so replacement is pixel-aligned. Direction reversal and
axis/volume changes advance generation and cancel obsolete work. Fine data
cannot replace a newer viewport. Missing levels fall back explicitly to the
finest available level.

## Axis assembly and bounded memory

XY requests one Z chunk slab over the Y/X grid. XZ requests one Y slab over
Z/X. YZ requests one X slab over Z/Y. No output pixel causes an HTTP request.
Assemblers allocate exactly one typed output plane. The Phase 13 decoded-chunk
LRU remains byte-bounded at 64 MiB by default. Encoded frame object URLs remain
owned by the established viewer caches (16 task-editor images; 256 read-only
images) and are revoked on eviction/unmount. Each mounted viewer owns its
queue/cache and token provider, which are disposed on unmount or volume change.
No decoded plane is stored in React state or persistent storage.

## Annotation and navigation preservation

The image layer changes; source/reference masks, editable working labels,
selected label, compositing, Track/Labels/Box/Point/brush/erase/interpolation,
undo, Save, autosave and recovery do not. All writes remain in source voxel
coordinates and under `MITO_DATA_ROOT`. The existing TIFF path remains the
rollback path and is required in every feature-flag smoke matrix.
