# ADR-004 — Dashboards and statistics: aggregate in the database, derive time from timestamps

**Status:** accepted, 2026-07-29
**Phase:** 6 (dashboards & statistics)
**Depends on:** Phases 3–5 (per phase map), PostgreSQL
**Gate (phase map):** manager acceptance
**Related:** ADR-001 (evidence before rewrites), ADR-002 (batching on measurement), ADR-003 (review loop)

---

## 1. Authoritative scope

| Source | Says |
|---|---|
| `CLAUDE_CODE_MASTER_PROMPT.md` §E6 | "Instance bar charts; time tracking; CSV; integrate People pages. Inspiration: WK project progress + time tracking docs." |
| `27-claude-code-phase-map.md` row 6 | "Dashboards & statistics", depends on **3–5**, gate **"manager acceptance"** |
| `14-complete-feature-gap-matrix.md` | Progress dashboards — mito has "lifecycle tabs / `lifecycle.py`", verdict **"Enrich"** (not replace) |
| `23-target-observability-design.md` | Manager dashboard signals: tasks/hour, rejection rate, hard-case rate, mean time-to-approve. *(That document is Phase 19; its manager list is used here as the definition of "statistics", not its Prometheus/Grafana stack.)* |

### Required

1. **Instance bar charts** — per-project distribution of task instances across
   states, the Phase 2/3 data that nothing currently surfaces.
2. **Time tracking** — see §2, conflict A. Durations **derived from existing
   timestamps**, not an invented activity accumulator.
3. **CSV export** of the statistics.
4. **People page integration** — see §2, conflict B. Backend aggregates the
   People surfaces consume; their frontend is untouched.

### Explicitly excluded

- Prometheus, Grafana, OpenTelemetry, `/healthz` — doc 23 is **Phase 19**.
- Annotation operation model, op logs → Phase 7.
- Any frontend change. See conflict B.
- Scheduler HTTP controls / dry-run approval UI. Phase 5 recorded these as
  Phase 6 candidates, but §E6 does **not** list them; see conflict C.
- New activity-tracking instrumentation.

### Responsibilities

| Layer | Owns |
|---|---|
| Backend service (`core/statistics.py`) | Every aggregate. Pure functions over querysets, no HTTP knowledge. |
| API (`core/statistics_api.py`) | Permission checks, serialization, CSV rendering. No business logic. |
| Frontend | **Nothing in this phase.** |

| Item | Decision |
|---|---|
| Endpoints | `GET /api/statistics/project/<pk>/`, `.../project/<pk>/export/`, `GET /api/statistics/annotators/` |
| Permissions | Project stats: project members (reuses `is_project_member`). Annotator stats: managers only. Never widened. |
| Models / migrations | **None.** Every figure is derivable from existing columns; see §3. |
| Flag | `FEATURE_DASHBOARDS`, default **False** |
| Audit | None. These are read-only reports; auditing every dashboard view would drown the log that Phase 1 built for permission changes. |
| Concurrency | Read-only. No locks, no transactions beyond the implicit one. |
| Performance | Query count **constant in row count**; no endpoint may load per-row objects to count them. |

## 2. Conflicts found, and how they were resolved

### Conflict A — "time tracking" versus what the schema records

WEBKNOSSOS accumulates `tracingTime` from actual annotation activity. **mito has
no equivalent field and no activity instrumentation.** Adding one would mean
inventing a data source, which the instruction for this phase explicitly
forbids.

What mito *does* have is timestamps: `AnnotationTask.{created_at, assigned_at,
submitted_at, approved_at}` and `TaskInstance.{claimed_at, started_at,
submitted_at, completed_at}`.

**Resolution — derive, and name honestly.** Phase 6 reports *elapsed durations
between recorded events*:

- **time-to-submit** — `submitted_at − assigned_at`
- **time-to-approve** — `approved_at − submitted_at`
- **cycle time** — `approved_at − created_at`

These are **wall-clock elapsed time, not time spent working.** A task assigned
on Friday and submitted on Monday shows three days regardless of effort. Every
label in the payload and the CSV says `elapsed`, never `time spent` or
`tracking`, so no consumer can mistake one for the other. Real effort tracking
needs the Phase 7 operation log and is out of scope here.

### Conflict B — "integrate People pages" versus untouchable WIP

`frontend/src/pages/{PeoplePage,PersonPage}.tsx` and their API modules are the
repository owner's **uncommitted work in progress**, which this pass is
required to preserve and forbidden to edit.

**Resolution.** Integration is delivered as the **backend aggregate** those
pages consume — `annotator_statistics()`, exposed at
`/api/statistics/annotators/` — and the frontend wiring is left to the author.
The existing `accounts/services.py` People helpers (`annotator_task_counts`,
`people_overview`) are **not modified**; the new service complements them with
the instance-level and duration figures they have no source for today.

This is the least destructive reading: it delivers the data half of the
requirement without editing a single line of someone else's in-flight feature.

### Conflict C — scheduler controls

The Phase 5 report listed "scheduler HTTP controls and `apply_plan` wiring" as
Phase 6 work. The **written** §E6 does not mention them; it lists bar charts,
time tracking, CSV and People pages.

**Resolution.** The written specification governs over a prior summary's
forward-looking guess. `apply_plan` stays unwired and no scheduler endpoint is
added. Recorded so the discrepancy is visible rather than silently dropped.

## 3. Decision

### Aggregate in the database, never in Python

The existing `projects.services.calculate_project_progress` is the shape to
avoid:

```python
for task in tasks.only("status"):
    status_counts[task.status] = status_counts.get(task.status, 0) + 1
```

That transfers and instantiates **every task row** to produce six integers. It
is the dashboard path, so fixing it is in scope rather than unrelated cleanup —
and it is exactly the pattern the phase requirements name ("avoid loading
complete task, audit, or submission histories when aggregate database queries
suffice").

Every Phase 6 aggregate is a `values(...).annotate(Count/Avg)` — one grouped
query per chart, constant in row count. Durations use PostgreSQL interval
arithmetic through `ExpressionWrapper`, so no timestamp arithmetic happens in
Python either.

### No new models, no migrations

Every required figure is derivable from columns that already exist. Adding a
denormalized statistics table would introduce a counter that can drift from the
rows it summarizes — the failure mode Phase 2 spent a CHECK constraint and a
reconciliation function avoiding. If measurement later shows an aggregate is too
slow, the fix is an index, then a materialized view, in that order, on evidence.

### Indexes only if measured

No index ships speculatively. The benchmark reports latency at increasing row
counts; an index is added only where it shows a real effect, and its effect is
recorded.

### Bounded by construction

`/annotators/` is capped and paginated. No endpoint returns an unbounded list.
CSV streams from the same aggregate as the JSON, so the two cannot disagree.

## 4. Compatibility

`FEATURE_DASHBOARDS` gates only the **new endpoints**, which 503 when off — the
same convention Phase 3 and 4 use, so a misconfiguration reads as "not enabled"
rather than a 404 typo.

The `calculate_project_progress` fix is **not** behind the flag: it changes how
a number is computed, not what the number is, and identical output is asserted
by test against the old implementation. A correctness/performance fix hidden
behind a feature flag would mean shipping the slow path to everyone who has not
opted in.

Phase 6 reads Phase 2–5 data but requires none of their flags. With every flag
off, the project dashboard still reports task-status counts and durations; the
instance chart is simply empty, because no instances exist. Asserted in the
smoke matrix rather than assumed.

## 5. Acceptance criteria

1. Query count per endpoint is **constant** as rows grow.
2. No endpoint instantiates a model object per counted row.
3. `calculate_project_progress` returns byte-identical output to the previous
   implementation, in fewer queries and without loading rows.
4. Permissions: a non-member gets 403 on project stats; a non-manager gets 403
   on annotator stats.
5. CSV and JSON report the same numbers.
6. Empty, paused and exhausted projects return well-formed zeroes, not errors.
7. With `FEATURE_DASHBOARDS` off, every new endpoint 503s and nothing else
   changes.
