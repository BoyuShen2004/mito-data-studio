# Integrated WEBKNOSSOS upgrade profile

## Safety boundary

The existing public v1.0.0 deployment remains a frozen `legacy` profile. This
work does not edit its environment, route, database, service, or built assets.
The integrated candidate is built and tested from the transformation branch in
an isolated checkout/database/data root before any later cutover decision.

## Coherent activation

`MITO_UPGRADE_PROFILE=webknossos` enables the completed backend phases as one
profile. Explicit `FEATURE_*` values still win as narrow rollback controls.
`VITE_MITO_UPGRADE_PROFILE=webknossos` supplies matching compile-time defaults
for PullQueue and the chunk renderer; `npm run build:upgrade` is the canonical
candidate command. For the live `production_integrated_v1` profile,
`FEATURE_VOLUME_PYRAMIDS` and `FEATURE_CHUNK_SERVICE` default on and
`npm run build:production` explicitly compiles both frontend flags on.

The parity staging environment explicitly declares all flags false. Therefore
the full candidate must apply `ops/staging/upgrade.env.example`, which explicitly
replaces all twelve backend values as well as both frontend values. Merely
changing the profile name while retaining the old false values is detected as
`deployment.W021` and rejected by the full candidate gate.

Startup checks reject dependency violations:

- claim or auto-fill without task hierarchy;
- autosave/recovery without annotation operations;
- chunk service without pyramids;
- frontend PullQueue without chunk service;
- renderer without PullQueue.

## Integrated user paths

- Annotators receive `Get next task` in the existing dashboard visual language
  only when the server reports task claiming enabled.
- Project pages add workflow slot/review timing statistics only when dashboard
  aggregates are enabled.
- The familiar Canvas proofreading UI stays mounted. The upgrade changes its
  image transport, operation durability, recovery, interpolation, and tools,
  not its overall navigation or interaction model.
- Existing manual assignment, TIFF/PNG reads, Save, review, hard-case, SAM and
  registration paths remain available as compatibility/fallback paths.

## Later-phase integration

- Phase 15 uses mito's existing bounded, cancellable per-label marching-cubes
  path and Three.js geometry disposal instead of replacing the familiar 3D
  labels panel.
- Phase 16 retains authenticated hard-case pages, revocable public tokens and
  coordinate/label deep links on the same annotation canvas.
- Phase 17 retains the existing EfficientSAM and fork-aware SAM2 endpoints;
  batch model commands are represented as durable `ProcessingJob` rows with
  input/output lineage.
- Phase 18 now executes shell-free argv locally through an operator allow-list
  or submits an equivalently quoted private script through Slurm. Dispatch,
  polling, cancellation and retry use the same persisted job lifecycle.
- Phase 19 adds operational health and bounded metrics without changing the
  application navigation.
- Phase 20 gates the data path with unit, concurrency, controlled scrub,
  browser rendering and memory-soak tests; the restored-data multi-user soak
  remains an isolated staging promotion gate.

## Operational maturity

- `/healthz`: public process liveness, no infrastructure details.
- `/readyz`: database, writable data root and disk watermark readiness.
- `/metrics`: Prometheus text, hidden unless a bearer token is configured.
- every HTTP response carries an opaque `X-Request-ID`;
- request, queue and chunk signals are bounded and low-cardinality;
- real local processing accepts shell-free argv only through an executable
  allow-list and writes a versioned SHA-256 artifact manifest;
- SLURM can generate a private submission script from the same argv contract.

## Mandatory candidate gate

```bash
MITO_UPGRADE_PROFILE=webknossos \
VITE_FEATURE_CHUNK_PULL_QUEUE=true \
VITE_FEATURE_CHUNK_RENDERER=true \
python backend/manage.py verify_upgrade_readiness --strict --json
```

The gate is read-only. It checks migration state, hierarchy backfill, instance
accounting, default teams, dependency checks, and reports pyramid rollout
inventory. A source volume without a derivative remains valid and uses the
established fallback while its pyramid is built.

## Remaining external gates

Production promotion still requires a protected database/data backup, a fresh
isolated candidate checkout, authenticated multi-user browser soak, public
identity verification and an explicit ingress cutover decision. New
registrations enqueue pyramids automatically; legacy volumes use the manager's
Build/Rebuild action instead of a mandatory deployment-wide backfill. Choosing
a repository-wide distribution license remains an owner/legal decision; no
upstream WEBKNOSSOS source was copied by this integration work.
