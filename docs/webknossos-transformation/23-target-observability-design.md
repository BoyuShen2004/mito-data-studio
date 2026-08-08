# 23 — Target Observability Design

## Goals

Operators must see, within minutes: assignment failures, chunk latency spikes, autosave backlog, disk pressure, and soak memory growth.

## Components

| Component | Tooling suggestion | Key signals |
|---|---|---|
| Metrics | Prometheus + Grafana | `http_request_duration_seconds`, `chunk_fetch_seconds`, `task_claim_conflicts_total`, `autosave_pending_ops`, `worker_queue_depth` |
| Logs | structlog JSON → Loki/ELK | `request_id`, `user_id`, `task_id`, `assignment_id`, `chunk_key` |
| Traces | OpenTelemetry | Django → chunk svc → DB |
| Health | `/healthz` liveness, `/readyz` deps | DB, Redis, chunk svc, disk watermark |
| Audit | append-only `AuditEvent` | claim, assign override, review, permission deny, scheduler decision |
| Errors | Sentry or equivalent | FE + BE; scrub tokens |

## Product dashboards

1. **Ops:** RED metrics for API + chunk service.
2. **Manager:** tasks/hour, rejection rate, hard-case rate, mean time-to-approve.
3. **Scheduler:** available users, starve rate, score distributions.

## SLOs (initial)

| SLO | Objective |
|---|---|
| API availability | 99.5% monthly (non-chunk) |
| Chunk p95 | < 150ms local SSD warm |
| Claim success | > 99% when work exists |
| Autosave durability | 0 acknowledged-op loss |

Tune after Phase 0 baselines.
