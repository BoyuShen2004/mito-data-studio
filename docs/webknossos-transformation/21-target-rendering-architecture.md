# 21 — Target Rendering Architecture

## Strategy

**Do not rip out the familiar Canvas proofreading UI in phase 1.**

Phased approach:

1. **Keep Canvas2D editor** as primary interaction surface.
2. Replace `<img>`/naive slice fetches with **chunk-backed slice assembly** (still Canvas) — big latency win without UX shock.
3. Introduce **WebGL overlay layer** for segmentation colorize/large IDs when Canvas `putImageData` becomes bottleneck.
4. Optionally add orthographic triple-view later; not required for mito MVP of WK-quality loading.
5. Upgrade three.js mesh panel with disposal, LOD, and server/chunked meshes.

## Frontend chunk manager requirements

- View-dependent requests; mag selection
- Priority queue; dedupe; abort; stale rejection
- Prefetch ±N along scrub axis
- Bounded LRU; GPU/CPU memory budgets
- Metrics: hit rate, p95 slice latency, in-flight count

## Performance targets (initial; refine in Phase 0 benchmarks)

| Metric | Target |
|---|---|
| Time to first useful slice | < 1.0s warm cache; < 3s cold (same LAN) |
| p95 slice step while scrubbing | < 100ms after warmup |
| In-flight chunk requests cap | ≤ 12 |
| Browser heap during 2h soak | flat within +15% after warmup |
| Autosave ack | < 2s p95 for typical stroke |
