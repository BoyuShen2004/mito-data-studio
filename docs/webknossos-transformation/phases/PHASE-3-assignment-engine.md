# Phase 3 — Pull-based assignment engine

**Status:** complete, shipped inert behind `FEATURE_TASK_CLAIM`
**Date:** 2026-07-28
**Depends on:** Phase 2 (task hierarchy), PostgreSQL
**Gate:** flag stays off until a product decision to switch annotators to pull

---

## Objective

Annotators **take** work instead of waiting to be given it:

```
POST /api/tasks/claim-next/
  → resolve eligibility (team, experience, capacity)
  → atomically claim one instance (pending_instances--)
  → return the task + a deep link to the editor
```

## Concurrency strategy — SKIP LOCKED, on evidence

The master prompt offers two designs. This implements **option 2**, and the
choice was measured rather than assumed:

| | WK-like (Serializable + retry) | **Chosen: SKIP LOCKED** |
|---|---|---|
| Collision becomes | transaction abort → retry | a *miss* → step to the next row |
| Retry budget needed | 50 (upstream's `retryCount`) | 5 |
| Behaviour when work is plentiful | contention still aborts | no contention at all |

A collision under SKIP LOCKED is not an error: the worker steps over the locked
row and takes the next one. That is why the retry budget is 5 rather than
upstream's 50 — retries are a safety net here, not the primary mechanism.

The read that picks a candidate and the write that takes it are **one
transaction**, and the task row is locked *before* `pending_instances` is read.
That ordering is what makes the decision safe; reading the counter first and
locking afterwards would let two claimants both see a free slot.

## What shipped

| Area | Detail |
|---|---|
| Engine | `annotation/claim.py` — claim, peek, assign, transfer, release, heartbeat, reclaim |
| HTTP | `annotation/claim_api.py` — 6 endpoints |
| Model | `TaskInstance.{heartbeat_at, lease_expires_at, claim_key}` + 2 constraints |
| Migration | `annotation.0009_claim_leases_and_idempotency` — **additive only** |
| Flag | `FEATURE_TASK_CLAIM`, default **False**, *and* requires `FEATURE_TASK_HIERARCHY` |
| Tests | `annotation/test_claim.py` — **75 tests** |
| Benchmark | `benchmarks/bench_claim_engine.py` |

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/tasks/claim-next/` | Take the next eligible task |
| GET | `/api/tasks/peek-next/` | What *would* be claimed — no locks, no writes |
| POST | `/api/tasks/<pk>/assign-instance/` | Manager push assignment |
| POST | `/api/task-instances/<pk>/transfer/` | Move work between annotators |
| POST | `/api/task-instances/<pk>/release/` | Hand work back to the pool |
| POST | `/api/task-instances/<pk>/heartbeat/` | Keep a lease alive |

Routes are registered **unconditionally** and return `503` when the flag is off.
Routing that appears and disappears with a flag makes a misconfiguration look
like a 404 typo, which is far harder to diagnose than an explicit "not enabled".

`204 No Content` means the queue is empty — an ordinary state for an annotator
who is up to date, not an error. `409` is capacity, `403` ineligibility.

## Idempotency — enforced by the database

A claim request that times out at the network layer is the one case where the
client genuinely cannot know whether it succeeded. Supplying `claim_key` makes
the retry safe: the replay returns the instance already claimed.

The service-layer check alone **cannot** provide this — two concurrent replays
both read before either writes. A partial unique index on
`(assigned_to, claim_key)` adjudicates instead, and `claim_next` catches the
resulting `IntegrityError` and returns the winner's instance.

> This was found by the concurrency test, not by inspection. The first
> implementation let the `IntegrityError` escape to the caller — meaning the
> retry, the exact case the key exists to serve, failed hardest of all.
> `test_concurrent_replay_of_one_key_claims_once` now asserts all six racing
> replays return the *same* instance with **zero** errors.

The key is scoped **per user**, not globally, so two annotators independently
retrying cannot collide on a shared key.

## Worker failure recovery — claims are leases

A crashed browser must not strand a slot forever; without recovery every crash
permanently shrinks the amount of claimable work.

- `lease_expires_at` is a concrete timestamp (not a computed expression), so the
  sweep is one indexed range scan — `idx_instance_lease_sweep`.
- An open editor calls `heartbeat` to extend it, so an annotator who is actually
  working never loses their claim.
- `reclaim_expired()` cancels expired instances and returns the slot, in **one
  transaction per instance** so a single bad row cannot roll back a whole sweep,
  and with `skip_locked` so two schedulers divide the work rather than deadlock.
- It re-checks the lease **under the lock**: a heartbeat may have landed between
  the scan and the update.

`test_reclaim_runs_safely_alongside_claiming` runs the sweeper concurrently with
20 live claimants and asserts no corruption.

## Backward compatibility

`assigned_to`, `status` and `task_type` remain authoritative for the existing UI.
For a single-instance task, claiming keeps them coherent (`assigned_to`,
`status=assigned`, `assigned_at`), and reclaiming resets them. For a
multi-instance task they are deliberately **left alone** — there is no single
assignee to record. Both behaviours are asserted.

## Measured behaviour

`bench_claim_engine.py`, PostgreSQL 16, 2026-07-28. It drives the **real**
`claim_next`, not a raw `select_for_update`, so the numbers describe the shipped
engine.

### Correctness under contention — all clean

| Scenario | Claims | Double claims | Counter drift | Negative pending | Deadlocks | Hard errors |
|---|---|---|---|---|---|---|
| 20 workers / 1 slot | 1 | **0** | **0** | **0** | **0** | **0** |
| 20 workers / 20 slots | 20 | **0** | **0** | **0** | **0** | **0** |
| 32 workers / 200×3 slots | 32 | **0** | **0** | **0** | **0** | **0** |

The acceptance test — 20 workers, 1 instance → exactly one winner — passes with
the other 19 receiving a clean `no_work`, not a lock error. That second half is
the part SQLite could never satisfy.

### Latency and throughput — the honest picture

100 free tasks, so there is **no** contention over work; this isolates the cost
of the claim path itself.

| Workers | p50 | Wall | Throughput |
|---|---|---|---|
| 1 | **68 ms** | 68 ms | 14.6/s |
| 2 | 103 ms | 105 ms | 19.0/s |
| 4 | 137 ms | 147 ms | 27.1/s |
| 8 | 302 ms | 341 ms | 23.5/s |
| 20 | 1083 ms | 1254 ms | 16.0/s |

**Latency scales roughly linearly with worker count while throughput plateaus at
16–27 claims/s.** That is serialization, and it should be read carefully:

- **Do not read these as server-side latencies.** The harness runs N Python
  *threads* in one process. The GIL serialises the Python half of every claim,
  and `CONN_MAX_AGE=0` means each thread pays fresh connection setup. A real
  deployment runs multiple gunicorn *processes*, which is the case this harness
  is least able to represent.
- The single-worker **68 ms** floor is the meaningful figure, and it is not
  attributable to threading. One claim costs roughly **7 round trips**: capacity
  `COUNT`, eligibility `SELECT`, `SELECT … FOR UPDATE`, duplicate re-check,
  instance `INSERT`, counter `UPDATE`, legacy-field `UPDATE`, plus an audit
  `INSERT`.

**Optimisation already applied.** `eligible_tasks()` originally materialised the
*entire* candidate queue to use its first element, making every claim O(open
tasks). Making it a generator cut the 32-worker case from p50 2025 ms → 1478 ms
(−27 %) and lifted throughput 13.5 → 17.5/s. It did **not** move the 20-worker
/ 20-task case, which correctly identified the remaining cost as round trips and
GIL, not queue scanning.

**Not yet done, and deliberately so:** collapsing those ~7 round trips (a
single `UPDATE … WHERE pending_instances > 0 RETURNING`, dropping the capacity
`COUNT` in favour of a maintained counter, making the audit write async). Per
ADR-001 these are exactly the kind of measurements that must precede any
architectural conclusion — and they show the cost here is **round trips and
Python-level serialisation, not the choice of language or framework.**

## Not in this phase

- No `AnnotationSession` model. The master prompt's flow names one, but there is
  no editor-session concept in the codebase to attach it to; the deep link
  (`editor_url`) covers the actual need today. Deferred rather than invented.
- No UI. The endpoints exist; no frontend calls them yet.
- No auto-fill / push scheduler — that is Phase 4.
- `AnnotationTask.status` is still not derived from instance states.
