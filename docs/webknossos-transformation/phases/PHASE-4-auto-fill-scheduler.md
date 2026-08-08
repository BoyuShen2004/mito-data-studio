# Phase 4 — Auto-fill scheduler

**Status:** complete, shipped inert behind `FEATURE_AUTO_FILL_SCHEDULER`
**Date:** 2026-07-28
**Depends on:** Phase 3 (claim engine), PostgreSQL
**Gate (phase map):** dry-run demos
**Design record:** [ADR-002](../adr/ADR-002-auto-fill-scheduler-strategy.md)

---

## Objective

Phase 3 gave **pull** — an annotator asks for work. Phase 4 adds **push**: a
scheduler places work with available annotators who have idle capacity, plus a
**hybrid** dry-run mode where a manager reviews proposals before applying them.

## Flag naming — a documented deviation

The master prompt calls the flag `FEATURE_AUTO_FILL`. The most recent explicit
instruction names it `FEATURE_AUTO_FILL_SCHEDULER`, and that is what shipped.
Recorded here because the two documents disagree and the newer one wins.

Like `FEATURE_TASK_CLAIM`, it requires `FEATURE_TASK_HIERARCHY` as well: a
scheduler with no task instances to place would fail confusingly at runtime
rather than plainly at the gate.

## What shipped

| Area | Detail |
|---|---|
| Service | `annotation/scheduler.py` — availability, scoring, matching, batch apply |
| Model | `annotation.SchedulerDecision` — one audit row per tick, including dry runs and empty runs |
| Migration | `annotation.0010_scheduler_decision` — **additive only**, one new table |
| Mechanism | `manage.py auto_fill [--dry-run] [--project] [--limit] [--tick-key]` |
| Audit | `accounts.audit.record_audit_bulk` |
| Flag | `FEATURE_AUTO_FILL_SCHEDULER`, default **False** |
| Tests | `annotation/test_scheduler.py` — **62 tests** |
| Benchmarks | `benchmarks/bench_scheduler.py`, `benchmarks/profile_claim_path.py` |

Terminology is `available` / `idle_capacity` throughout, as the roadmap
requires.

## Why batched, not looped

The obvious implementation loops the Phase 3 `claim_next`. Research doc 18
recommends exactly that ("reuse `assign_instance` primitive"). It was measured
first.

`profile_claim_path.py` established the per-item cost: **11 SQL round trips and
~35 ms per claim, constant in queue size**. Constant *per item* means looping N
assignments costs 11 N round trips.

> **Correction.** The Phase 3 report estimated "roughly seven round trips per
> claim". The measured figure is 11. The estimate was read off the code; the
> profiler counted.

### Measured comparison — both implementations, same fixture

| Batch | Strategy | SQL queries | Wall time | Assignments/s |
|---|---|---|---|---|
| 1 | batched | 14 | 23.2 ms | 43 |
| 1 | looped | 12 | 35.9 ms | 28 |
| 10 | batched | **14** | **44.3 ms** | **226** |
| 10 | looped | 102 | 192.1 ms | 52 |
| 50 | batched | **14** | **72.7 ms** | **688** |
| 50 | looped | 510 | 1236.9 ms | 40 |
| 200 | batched | **14** | **196.6 ms** | **1017** |
| 200 | looped | 2040 | 6317.8 ms | 32 |

At 200 assignments: **146× fewer queries and 32× faster.** Query count is flat
at 14 from 1 to 200 assignments — verified again at 1/5/20/100/200 — while the
looped path grows linearly at ~10 queries per item.

**Batching is not free at N=1** (14 vs 12 queries): the fixed cost of reading
availability and writing the decision row is paid whether one assignment is made
or two hundred. It pays for itself from roughly the second assignment onward.
Stated because a benchmark that only reports its best case is not evidence.

`QueryCostTests` asserts the flat-query-count property in the suite, so a future
change that reintroduces per-item work fails a test rather than quietly
regressing.

## Correctness

Every invariant was measured at each batch size and under 1/2/4/8 concurrent
schedulers. **Zero** counter drift, duplicate instances, negative counters,
over-capacity annotators, deadlocks, and errors, in every configuration.

### Two bugs the tests caught

**1. Per-user capacity is not database-enforced.** ADR-002's first draft claimed
every guarantee was held by a schema constraint. `pending_instances >= 0` bounds
a *task*; nothing bounds one *annotator*. Three concurrent ticks each read the
same idle capacity and each filled it:

```
test_concurrent_schedulers_never_exceed_capacity
  AssertionError: ann-0 over-allocated with 12 instances (capacity 4)
```

Fixed with a transaction-scoped `pg_try_advisory_xact_lock`. Non-blocking: a
tick that cannot take the lock records a skipped decision and returns, so
schedulers never queue behind one another. Transaction-scoped, so a scheduler
killed mid-batch cannot strand it.

**Consequence, stated plainly:** multiple scheduler processes are **safe but not
parallel with each other**. They stay fully parallel with pull claimants, which
never take this lock. Dry runs skip it entirely — they write nothing.

**2. The audit write was the only per-item cost left.** 40 assignments produced
40 `INSERT`s into `accounts_auditevent`, so the tick was 50 queries rather than
14. `record_audit_bulk` collapses them into one statement, preserving the
best-effort semantics of `record_audit`: an audit failure must never roll back
real assignments.

### Concurrent schedulers

| Schedulers | Assignments | Ticks skipped (lock) | p50 | p95 | Drift | Dup | Over-cap | Errors |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 0 | 329.2 ms | 329.2 ms | 0 | 0 | 0 | 0 |
| 2 | 200 | 1 | 237.0 ms | 237.0 ms | 0 | 0 | 0 | 0 |
| 4 | 200 | 3 | 84.8 ms | 315.2 ms | 0 | 0 | 0 | 0 |
| 8 | 200 | 7 | 125.2 ms | 356.6 ms | 0 | 0 | 0 | 0 |

Exactly one tick does the work and the rest stand down immediately — the
designed behaviour, and the total is 200 every time regardless of how many
schedulers race.

## Scheduling rules

**Available annotator** (stricter than for pull, deliberately — someone who
calls `claim-next` has demonstrated they are at their desk; someone receiving
pushed work has not):

- active account and active annotator profile
- seen within `MITO_SCHEDULER_ACTIVE_DAYS` (default 14; `0` disables). A user
  who has **never** logged in is included — a fresh account must be reachable,
  not permanently invisible.
- `idle_capacity > 0`
- passes the same `meets_requirements` gate as pull (team access, experience),
  so push and pull cannot drift apart on who is allowed what

**Deterministic score**, weights in settings, each component normalised to 0..1:

```
score = w_project_priority · project_priority
      + w_task_priority    · task_priority
      + w_deadline_urgency · deadline_urgency
      + w_quality_history  · quality_history
      + w_fairness_bonus   · (1 − load_ratio)
      − w_current_load     · load_ratio
```

Overdue work saturates urgency at 1.0 rather than going negative — nothing is
more urgent than already late. `SchedulerDecision.decisions` records each
assignment's score *and its components*, so a disputed assignment can be
explained rather than merely reported.

Matching is greedy over priority-ordered tasks: for each task, the
highest-scoring eligible annotator with capacity. Greedy rather than globally
optimal on purpose — an optimal assignment needs the whole bipartite graph, and
the guarantee managers asked for is "higher-priority work is placed first",
which greedy gives exactly. `test_scheduling_is_deterministic` pins
reproducibility.

## Safety properties

| Property | How |
|---|---|
| Bounded work per tick | `LIMIT min(idle capacity, MITO_SCHEDULER_MAX_BATCH)`, default 200 |
| No unbounded scan | the same `LIMIT`, applied before the lock |
| No long-held lock | one bounded batch; advisory lock is `try`, never waits |
| Atomic | one transaction per batch; a mid-batch failure rolls back everything |
| Idempotent | `tick_key` unique; a replay reports the original result |
| Crash recovery | transaction-scoped lock releases on ROLLBACK; a rolled-back tick's key is free to retry |
| No lock-order deadlock | only task rows are locked, always in one order; no user row is ever locked |
| Paused projects | excluded in the candidate query |
| Exhausted tasks | `pending_instances > 0` filter |
| Abandoned pushed work | `lease_expires_at` + `reclaim_expired` — pushed instances are leases exactly like pulled ones |

## Backward compatibility

With the flag off, `run_auto_fill` raises and writes nothing; the management
command exits with a clear error. Legacy `assigned_to`/`status`/`assigned_at`
are maintained for single-instance tasks and left alone for multi-instance ones
— identical to the pull path, so the existing UI sees no difference in either.

## Not in this phase

- **No Celery or distributed queue.** ADR-001's rule: adopt infrastructure on
  evidence, not resemblance. Cron invoking a management command meets the gate
  with no new runtime dependency, and the algorithm is a pure service function,
  so a real queue can call it unchanged later.
- **No HTTP endpoint or UI** for triggering ticks or approving dry runs — the
  `apply_plan` service exists; wiring it to a manager screen belongs with the
  Phase 6 dashboards.
- **No `locality` or `rejection_rate` score components.** Doc 18 lists them;
  there is no data source for either yet, and inventing one would produce
  confident nonsense.
