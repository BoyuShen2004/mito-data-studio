# 10 — WEBKNOSSOS Performance and Stability

## Architectural strengths (evidence-based)

| Strength | Evidence |
|---|---|
| Service separation (app / datastore / tracingstore) | repo modules + docs |
| Chunked multi-res IO | datastore readers + frontend DataCube |
| Request prioritization & abortion | `pullqueue.ts` |
| Sparse annotation persistence | pushqueue + FossilDB tracingstore |
| DB-enforced task instance integrity | triggers + Serializable assign |
| Automated tests | vitest suites, backend tests, CircleCI badge |
| Docker deployability | docker-compose, Hub images |
| Long-session bucket eviction coordinated with save | eviction tests |

## Reliability mechanisms

- Annotation mutexes / session locks (evolutions 100, 165)
- Initializing annotation abort on failure
- Mapping locks during volume modifications
- Health check tooling (`admin/datastore_health_check.ts`)
- Telemetry hooks (`@airbrake/browser` dependency)

## What “WEBKNOSSOS-quality” means for mito targets

Measurable, not vibes — see `25-testing-benchmark-and-soak-strategy.md`.
