# Chunk renderer release decision

## Decision

`VITE_FEATURE_CHUNK_RENDERER=false` remains the production setting for the
TIFF-based v1.0 release. The Phase 13/14 implementation stays available for
private qualification, but it is not production-ready on the restored 2048²
volume. This is an experimental-feature deferral, not a blocker for the TIFF
release.

## Restored-data evidence

The representative volume has source shape `256 x 2048 x 2048`. At mag 1 its
chunk grid is `256 x 4 x 4` with `1 x 512 x 512` chunks; mag 2 is `256 x 2 x 2`
and mag 4 is `128 x 1 x 1`. A 20-step real sequential scrub measured:

| Path | p50 | p95 | Requests | Bytes |
|---|---:|---:|---:|---:|
| TIFF/PNG | about 380 ms | about 473 ms | 22 | 26.8 MB |
| chunk renderer | about 775 ms | about 3382 ms | 370 | 96.5 MB |

The browser viewport was approximately 800 x 600 CSS pixels. The chunk path
therefore transferred and assembled substantially more data than the visible
region required.

## Confirmed cause

`ChunkDataSource.loadPlane` schedules every chunk intersecting the complete
orthogonal source plane. For a mag-1 XY plane that is all 16 chunks, or 4 MiB
of uint8 voxels, before full-plane assembly and canvas conversion. Moving
requests intentionally fetch a coarse plane and then refine to a complete fine
plane. Per-chunk HTTP requests and coarse-to-fine refinement explain the
observed request/byte amplification. Canceled neighbor or superseded work adds
some overhead, but request identity, in-flight collapse, build-identity cache
keys, and stale-generation rejection are already present; the evidence does
not support missing deduplication or cache-key churn as the primary defect.

XZ/YZ correctly request only the chunk slab intersecting the fixed coordinate,
but still assemble the full output plane. No path requests one call per output
pixel.

## Why no release-closure code change

A correct fix requires viewport-aware chunk-range calculation, alignment of
CSS/canvas/source coordinates, and likely request aggregation or server-side
plane/tile transport. That changes Phase 13/14 rendering contracts and needs
new pixel-correctness, annotation-coordinate, cancellation, browser, and
memory tests. It is not a focused low-risk release fix.

Post-release qualification should add viewport-tiled plane reads, cap
directional prefetch during rapid scrub, measure coarse and fine work
separately, and evaluate bundled transport. TIFF fallback must remain even if
the experimental path later becomes faster.
