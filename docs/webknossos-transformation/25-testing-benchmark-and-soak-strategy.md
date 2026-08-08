# 25 — Testing, Benchmark, and Soak Strategy

## Phase 0 baseline (before redesign)

Measure on representative large volumes:

- TTFV / TTI
- p50/p95 slider, layer switch, zoom/pan
- label paint latency; mesh latency
- API & disk metrics; browser/GPU memory
- multi-user contention smoke

Store results under `docs/webknossos-transformation/benchmarks/`.

## Test pyramid additions

| Layer | Additions |
|---|---|
| Unit | SDF interpolation; scorer; state machine |
| Integration | claim races with concurrent workers (Postgres) |
| API | permissions matrix |
| Frontend | Playwright smoke for editor save/submit |
| Load | k6/locust chunk + claim endpoints |
| Soak | 4h single-session editor; 2h multi-user |

## Definition gates

No phase marked done without listed acceptance tests green.
