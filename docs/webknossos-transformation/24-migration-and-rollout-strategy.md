# 24 — Migration and Rollout Strategy

## Approval gates (mandatory stop points)

1. Research confirmation (this doc set) — **current gate**
2. Architecture approval
3. Schema migration approval
4. Task-management rollout
5. Annotation rollout
6. Volume-infrastructure rollout
7. Production migration

## Data migration principles

- Dual-write / feature flags where possible
- Expand-contract migrations
- Backfill TaskType/Instances from AnnotationTask
- Never delete production annotations
- Dry-run SQL + row counts + checksum samples
- Rollback scripts per phase

## Environment path

SQLite-dev → Postgres-dev → staging soak → production.

## Deploy

Introduce Docker Compose (app, worker, postgres, redis, chunk svc) without forcing Cloudflare changes until approved.
