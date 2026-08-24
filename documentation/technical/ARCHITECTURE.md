# System architecture

## Component overview

```text
Browser (React + TypeScript + Canvas/Three.js)
        |
        | HTTPS / JSON / PNG or chunk payloads
        v
Django REST API (gunicorn)
        |                  \
        |                   \ queued heavy jobs
        v                    v
PostgreSQL/SQLite       Processing dispatcher
metadata/workflow       local adapter or SLURM
        |
        v
MITO_DATA_ROOT / uploaded storage
source volumes, working labels, snapshots, embeddings, pyramids, logs
```

The production-shaped Docker deployment serves the compiled single-page
application with WhiteNoise from the Django/gunicorn process and runs
PostgreSQL as a separate service. The full development Docker stack uses the
same basic topology. A host-development option runs Django and Vite separately.

## Backend organization

| Django app | Responsibility |
| --- | --- |
| `accounts` | Authentication, profiles, roles, institutions, teams, people, audit events |
| `projects` | Projects, datasets, memberships, lifecycle, and public shares |
| `volumes` | Volume registration, metadata, pairing, ROI coverage, pyramids, chunk serving |
| `annotation` | Tasks, editing plans, labels, Track, AI prompts, submissions, reviews, hard cases, timing |
| `processing` | Durable jobs, dispatcher, local execution, and SLURM integration |
| `core` | Shared choices, storage ownership, security checks, maintenance, observability, reset safety |

Business rules live in service modules rather than views. Django REST Framework
views authenticate, authorize, decode input, call services, and serialize
results. Database migrations are additive and form part of the release record.

## Frontend organization

The frontend is a React 18/TypeScript single-page application using React
Router. Role-aware routes provide manager, requester, and annotator dashboards;
shared project/volume/task pages; a full-window viewer/editor; hard-case and
review surfaces; profile/people pages; and unauthenticated read-only shares.

The annotation canvas combines browser-side pending label slices with
server-planned operations. This allows previews and compound Undo/Redo without
persisting every intermediate model or deterministic-tool result.

## Core data flow

1. Registration stores a validated source path and inspected metadata.
2. Assignment creates or updates a whole-volume annotation task.
3. The viewer reads source slices or validated pyramid chunks.
4. Editing tools create browser-side pending planes or server-returned plans.
5. Save writes revision-checked working-label planes and provenance metadata.
6. Submit creates an immutable snapshot for one submission channel.
7. Manager approval promotes the chosen snapshot to the official label and
   creates a fresh working state.

## Plan/preview/apply pattern

Whole-volume or model-assisted operations preferentially return planned slices
with before/after run-length encodings. The browser applies them to its pending
buffer. This pattern keeps preview and rejection local, avoids silent
persistence, and makes compound changes reversible before Save.

SAM2 Track uses a bounded z slab spanning queued ranges. Classes are applied in
request order so earlier classes win protected collisions. The SAM2 adapter is
a process-local singleton protected by a re-entrant lock because its inference
state is mutable.

## Background processing

`ProcessingJob` records support inspect, ingest, predict, seed, task generation,
quality control, visualization conversion, mesh generation, pyramid build, and
publish job types. A dispatcher claims queued work and uses either a restricted
local adapter or SLURM. Heavy background work is designed not to run inside an
ordinary HTTP request; synchronous Track is the documented exception and is
therefore sized conservatively.

## Integrity and security boundaries

- Registered images and region masks are read-only inputs.
- Owned working artifacts are restricted to the configured data root.
- Writes use path ownership checks and serialized file-write locks.
- CSRF, authenticated sessions, server-side permission checks, and optional
  TLS/proxy hardening protect state-changing APIs.
- Public tokens expose read-only scoped views and can be revoked.
- Production reset requires authentication, password re-check, an exact
  phrase, a fresh backup marker, maintenance mode, and deployment identity.
- Development reset is separately gated and must never be enabled on valuable
  deployments.

## Versioned implementation stack

The release lock currently specifies Django 5.1.15, Django REST Framework
3.17.1, NumPy 2.4.6, SciPy 1.17.1, scikit-image 0.26.0, PyTorch 2.5.1 with CUDA
12.4 wheels, ONNX Runtime GPU 1.26.0, Zarr 3.1.6, tifffile 2026.3.3, h5py 3.16.0,
nibabel 5.3.2, gunicorn 26.0.0, and PostgreSQL through psycopg 3.3.4. The
frontend declares React 18.3.1, TypeScript 5.5.4, Vite 6.4.3, Three.js 0.170,
Vitest 4.1.10, and Playwright 1.62.1. Use lock files—not this prose—as the
installation authority.

