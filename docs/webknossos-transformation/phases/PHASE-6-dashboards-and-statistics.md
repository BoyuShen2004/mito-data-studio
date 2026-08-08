# Phase 6 — Dashboards and statistics

**Status:** complete, shipped inert behind `FEATURE_DASHBOARDS`
**Date:** 2026-07-29
**Depends on:** Phases 3–5 (per phase map), PostgreSQL
**Gate (phase map):** manager acceptance
**Design record:** [ADR-004](../adr/ADR-004-dashboards-and-statistics.md)

---

## Objective

Master prompt §E6: *"Instance bar charts; time tracking; CSV; integrate People
pages."* Gap matrix verdict for progress dashboards: **"Enrich"**, not replace.

## What shipped

| Area | Detail |
|---|---|
| Service | `core/statistics.py` — every aggregate, no HTTP knowledge |
| API | `core/statistics_api.py` — 3 read-only endpoints |
| Fix | `projects.services.calculate_project_progress` now aggregates in the database |
| Flag | `FEATURE_DASHBOARDS`, default **False** |
| Migrations | **None** — every figure derives from existing columns |
| Tests | `core/test_statistics.py` — **49 tests**; smoke matrix → 50 tests / 8 configs |
| Benchmark | `benchmarks/bench_dashboards.py` |

| Endpoint | Purpose |
|---|---|
| `GET /api/statistics/project/<pk>/` | One project's dashboard |
| `GET /api/statistics/project/<pk>/export/` | The same figures as CSV |
| `GET /api/statistics/annotators/` | Roster comparison, managers only, paginated |

## Conflicts resolved

**A — "time tracking" versus what the schema records.** WEBKNOSSOS accumulates
`tracingTime` from annotation activity. mito has no such field and no
instrumentation that could produce one, and inventing a data source was
explicitly out of bounds for this phase.

Resolved by deriving from timestamps that already exist and **naming them
honestly**: every key is `mean_elapsed_*`, never "time spent". These are
wall-clock intervals — a task assigned Friday and submitted Monday reads three
days regardless of effort. Real effort tracking needs the Phase 7 operation log.

**B — "integrate People pages" versus untouchable WIP.** The People pages are
the repository owner's uncommitted frontend work, which this pass must preserve
and may not edit. Integration therefore shipped as the **backend aggregate**
those pages consume (`/api/statistics/annotators/`), with frontend wiring left
to the author. `accounts/services.py` was not modified.

**C — scheduler controls.** The Phase 5 report predicted these would land in
Phase 6. The written §E6 does not mention them, so `apply_plan` stays unwired
and no scheduler endpoint was added. Recorded because a prior summary's guess
should not silently become scope.

## The bug this phase fixed

`calculate_project_progress` counted task statuses by iterating rows:

```python
for task in tasks.only("status"):
    status_counts[task.status] = status_counts.get(task.status, 0) + 1
```

It transferred and instantiated **every task row** to produce six integers. This
is the dashboard path, so fixing it was in scope rather than unrelated cleanup.

Not behind the flag, deliberately: it changes how a number is computed, not what
it is, and hiding a performance fix behind an opt-in would ship the slow path to
everyone who has not opted in. `test_matches_legacy_python_tally` asserts the
new implementation against an explicit reimplementation of the old loop, so
"identical output" is verified rather than assumed.

## Measured behaviour

`bench_dashboards.py`, PostgreSQL 16. Query counts via `CaptureQueriesContext`,
database time from PostgreSQL, Python time as the remainder.

### Project dashboard — constant queries, constant payload

| Tasks | SQL | p50 | p95 | p99 | DB | Python | Payload |
|---|---|---|---|---|---|---|---|
| 100 | **6** | 7.7 ms | 9.9 ms | 9.9 ms | 3 ms | 4.7 ms | 739 B |
| 1 000 | **6** | 39.3 ms | 49.5 ms | 49.5 ms | 28 ms | 11.3 ms | 753 B |
| 10 000 | **6** | 39.3 ms | 49.9 ms | 49.9 ms | 35 ms | 4.3 ms | 767 B |

### Annotator statistics

| Tasks | SQL | p50 | p95 | DB | Python | Payload |
|---|---|---|---|---|---|---|
| 100 | **2** | 4.2 ms | 4.4 ms | 2 ms | 2.2 ms | 2.1 kB |
| 1 000 | **2** | 5.6 ms | 9.2 ms | 3 ms | 2.6 ms | 2.2 kB |
| 10 000 | **2** | 16.8 ms | 22.0 ms | 14 ms | 2.8 ms | 2.2 kB |

### The per-row implementation it replaced

| Tasks | SQL | p50 | DB | Python |
|---|---|---|---|---|
| 100 | 101 | 79.5 ms | 1 ms | 78.5 ms |
| 1 000 | 1 001 | 877.3 ms | 162 ms | 715.3 ms |
| 10 000 | 10 001 *(log capped at 9 000)* | 8 809.8 ms | 1 287 ms | 7 522.8 ms |

At 10 000 tasks: **~1 670× fewer queries and 224× faster**. Note where the time
went — the legacy path spent **7.5 s of 8.8 s in Python**, instantiating model
objects to increment counters. That is the specific failure mode the phase
requirements name, and it is why aggregation belongs in the database.

Payload size is flat (739 → 767 bytes across a 100× data increase), which is the
other half of the property: a dashboard whose response grows with the project is
as broken as one whose query count does.

> **A benchmark artifact worth recording.** The first run reported "0 queries"
> at 10 000 rows and the harness's own guard failed. The cause was Django
> capping `connection.queries` at 9 000 entries: the legacy path's 10 001
> queries blew the cap and corrupted every later capture. Fixed with
> `reset_queries()` before each measurement. The guard did its job — it caught a
> broken measurement rather than letting a wrong number be published.

### Indexes

**None added.** ADR-004 says no index ships speculatively, and at 10 000 tasks
the dashboard's database time is 35 ms across six grouped queries — acceptable
without one. If a project reaches a size where this degrades, the first
candidate is `(project_id, status)` on `annotation_annotationtask`, then a
materialized view, in that order and on evidence.

## Compatibility

`FEATURE_DASHBOARDS` gates only the new endpoints, which 503 when off — the
convention Phases 3–5 use, so a misconfiguration reads as "not enabled" rather
than a 404 typo.

It requires no other flag. Smoke configuration **H** proves it: dashboards on a
legacy single-assignee project return task statuses, durations and CSV, with an
empty instance chart — a correct answer, not an error.

Permissions reuse `is_project_member` rather than inventing a second rule, so
dashboard visibility cannot drift from task visibility and Phase 1 team grants
work here automatically. Cross-annotator figures are manager-only. **No
permission was widened or narrowed.**

## Known limits, stated rather than implied

- **"Elapsed" is not "time spent".** Repeated because it is the single most
  misreadable number in this phase.
- `/annotators/` is hard-capped at 200 per page. A deployment with more
  annotators must page; there is no "all" mode by design.
- Dashboard views are **not audited**. Auditing every read would drown the
  permission-change log Phase 1 built. If read auditing is ever required, it
  needs its own channel.
- Aggregates are computed live. There is no caching layer; at the measured
  sizes there does not need to be one.

## Not in this phase

- Prometheus, Grafana, OpenTelemetry, `/healthz` — doc 23 is **Phase 19**.
- Any frontend change, including wiring the People pages.
- Scheduler HTTP controls, dry-run approval UI, `apply_plan`.
- Annotation operation model and op logs → Phase 7.
