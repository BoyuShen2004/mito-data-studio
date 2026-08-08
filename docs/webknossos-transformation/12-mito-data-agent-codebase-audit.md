# 12 — mito-data-agent Codebase Audit

**Path:** `/home/weidf/shenb/mito-data-agent`
**Audit mode:** read-only (2026-07-27)

## Stack

| Layer | Reality |
|---|---|
| Backend | Django 5 + DRF, Token auth |
| DB | **SQLite** (`db.sqlite3`) — not Postgres |
| Frontend | React 18 + Vite + TS; no Redux; hand-rolled CSS |
| Storage | `MITO_DATA_ROOT` filesystem TIFF/NIfTI |
| Deploy | Dev scripts only; **no Docker/Cloudflare/gunicorn** in tree |
| Tests | ~290 backend tests; **no frontend tests** |

## Apps

`accounts`, `projects`, `volumes`, `annotation`, `processing`, `core`

## Domain models (summary)

Institution → Project → Dataset → Volume → AnnotationTask → Submission/Review/HardCase; ProcessingJob orthogonal.

## Subsystem classifications

| Subsystem | Class | Retain? |
|---|---|---|
| Roles + object ACL | usable–mature | retain & harden |
| Register HPC dirs + nnU-Net naming heuristics | mature | retain |
| Assign plan UI + rule assign | mature for single-node | redesign concurrency & hierarchy |
| Submit/review/lock | mature (mito strength) | retain |
| Hard cases + public token | usable–mature | retain & deepen deep-links |
| Canvas editor + EfficientSAM | mature UX | retain UI; upgrade internals |
| SAM2 tracking | usable | retain; fix whole-volume cost |
| Slice IO memmap | usable / scale-limited | replace delivery path |
| 3D three.js meshes | incomplete | upgrade |
| ProcessingJob/Slurm | naive scaffolding | complete or replace |
| nnU-Net train/predict | missing (heuristics only) | implement |
| Frontend tests / soak / metrics | missing | add |
| Multi-worker safety | unsafe (SQLite + process caches) | replace foundation |

## Key files

See agent audit evidence: `backend/annotation/services.py`, `slice_io.py`, `AnnotationCanvas.tsx`, `progress/PROJECT.md`.
