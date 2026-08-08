# ADR-002 — Auto-fill scheduler: batched SKIP LOCKED, not looped single-claim

**Status:** accepted, 2026-07-28
**Phase:** 4 (auto-fill scheduler)
**Depends on:** Phase 3 claim engine, ADR-001
**Supersedes on one point:** research doc `18-auto-fill-scheduler-design.md`,
which specifies *"Same claim transaction as pull (reuse `assign_instance`
primitive)"*. See §6.

---

## 1. Context

Phase 3 delivered **pull**: an annotator calls `claim-next` and takes one task.
Phase 4 adds **push**: a scheduler assigns work to available users who have idle
capacity, plus a **hybrid** dry-run mode where a manager reviews proposals
before they are applied.

The obvious implementation is to loop the Phase 3 primitive: for each available
user, call `claim_next` on their behalf. Doc 18 explicitly recommends this. It
was measured before being accepted, and rejected.

## 2. Measurement — the cost of the per-item path

`benchmarks/profile_claim_path.py`, PostgreSQL 16, `CaptureQueriesContext` plus
PostgreSQL-reported execution time. 30 samples per scenario.

| Scenario | Outcome | SQL queries | p50 | p95 | p99 | DB time | DB share |
|---|---|---|---|---|---|---|---|
| small queue, work available | claimed | **11** | 35.7 ms | 38.5 ms | 38.6 ms | 16 ms | 45 % |
| no work available | no_work | **2** | 5.9 ms | 10.5 ms | 12.9 ms | 2 ms | 34 % |
| all projects paused | no_work | **2** | 10.4 ms | 15.2 ms | 17.1 ms | 4 ms | 39 % |
| large queue (20 projects, 8 types, 1000 claimable) | claimed | **11** | 33.6 ms | 42.7 ms | 45.4 ms | 18 ms | 53 % |
| large queue, 90 % exhausted | claimed | **11** | 37.6 ms | 39.1 ms | 40.5 ms | 17 ms | 46 % |
| large queue, 80 % paused | claimed | **11** | 35.7 ms | 40.7 ms | 42.1 ms | 17 ms | 49 % |

**Correction to an earlier estimate.** The Phase 3 report estimated "roughly
seven round trips per claim". The measured figure is **11**. The estimate was
made by reading the code rather than counting queries; the profiler is
authoritative.

Two properties matter for this decision:

* **Query count is constant in queue size.** 11 whether 50 or 1000 tasks are
  claimable, and unchanged when most of the queue is paused or exhausted. The
  Phase 3 generator fix did its job — the eligibility scan is not a
  query-count problem.
* **Query count is therefore per *item*, not per *call*.** Looping N
  assignments costs 11 N round trips and ≈ 35 N ms. Scheduling 100 assignments
  would be ~1100 queries and ~3.5 s of mostly-latency.

The 11 queries, in order: capacity `COUNT`; eligibility cursor `DECLARE`;
`BEGIN`; `SELECT … FOR UPDATE SKIP LOCKED`; **a lazy `SELECT` of the project
row**; duplicate re-check `SELECT 1`; instance `INSERT`; counter `UPDATE`;
legacy-fields `UPDATE`; `COMMIT`; audit `INSERT`.

The lazy project `SELECT` is an N+1 (`locked.project.paused` on an unfetched
relation) and is fixed as part of this phase — it benefits the pull path too.

## 3. Options considered

| # | Strategy | Queries per batch of N | Verdict |
|---|---|---|---|
| 1 | Repeated single-claim (`claim_next` in a loop) | **11 N** | **Rejected** — measured above. Correct, but pays full per-item cost N times, and holds N separate transactions. |
| 2 | Batched candidate selection + bulk create/update | **~8 constant** | **Chosen**, combined with 4. |
| 3 | Lease / reservation table | ~8 + a reclaim sweep | **Rejected as redundant** — `TaskInstance` already *is* a lease (Phase 3 added `lease_expires_at`, heartbeat and `reclaim_expired`). A second reservation table would duplicate that machinery and create two sources of truth for "who holds this work". |
| 4 | PostgreSQL `SKIP LOCKED` batching | — | **Chosen** as the locking mechanism inside 2. |

### Why 2 + 4 together

Option 2 answers *how many round trips*; option 4 answers *how concurrent
schedulers stay correct*. They are complementary, not alternatives:

```sql
SELECT … FROM annotation_annotationtask
 WHERE pending_instances > 0 AND NOT project.paused …
 ORDER BY project.priority DESC, priority DESC, created_at, id
 LIMIT :budget
   FOR UPDATE OF annotation_annotationtask SKIP LOCKED
```

One statement locks a bounded batch. A second scheduler running concurrently
skips those rows and takes the next ones, so two schedulers **partition** the
queue instead of contending on it — the same property that made Phase 3's claim
path succeed 100 % of the time where SQLite managed 5–12 %.

## 4. Decision

Implement the scheduler as **batched selection under `SKIP LOCKED`, with bulk
instance creation**, in a service module, invoked by a mechanism kept separate
from the algorithm.

### Correctness rests on constraints already in the schema

Batching must not weaken the guarantees the per-item path gets for free. It
does not, because every one of them is enforced by the database rather than by
the service:

| Guarantee | Enforced by |
|---|---|
| No duplicate instance per (task, user) | `uniq_instance_per_task_user` partial unique index |
| No over-filling a *task* | `pending_instances_non_negative` CHECK |
| No two schedulers taking the same task | `FOR UPDATE … SKIP LOCKED` |
| Idempotent replay of one tick | `SchedulerDecision.tick_key` unique |

A batch that violates any of these fails at `COMMIT` rather than committing
corrupt state, which is why bulk creation is safe here and would not be in a
schema without them.

### Correction — per-user capacity is *not* database-enforced

An earlier revision of this ADR listed "no over-allocation" as fully covered by
the schema. **That was wrong, and a test caught it.**

`pending_instances >= 0` bounds how many times a *task* is handed out. Nothing
bounds how much work one *annotator* holds. Capacity is read before the
transaction, so two concurrent ticks each saw the same idle slots and each
filled them. Measured, before the fix:

```
test_concurrent_schedulers_never_exceed_capacity
  AssertionError: ann-0 over-allocated with 12 instances (capacity 4)
```

Three concurrent schedulers, one capacity-4 annotator, twelve assignments.

**Fix:** a transaction-scoped PostgreSQL advisory lock
(`pg_try_advisory_xact_lock`) makes *writing* ticks mutually exclusive. It is
`try`, not blocking: a tick that cannot take the lock records a skipped decision
and returns, so schedulers never queue behind each other and no lock is held
longer than one bounded batch. Being transaction-scoped, it is released on
`COMMIT` **or** `ROLLBACK`, so a scheduler killed mid-batch cannot strand it.

Consequence, stated plainly: **multiple scheduler processes are safe but not
parallel with each other.** They remain fully parallel with pull claimants,
which never take this lock. Given a tick places up to `MITO_SCHEDULER_MAX_BATCH`
assignments in ~14 queries, serialising ticks costs far less than the
correctness bug it removes. Dry runs skip the lock entirely — they write
nothing.

The alternative — locking each candidate annotator's row — was rejected: it
would introduce a second lock class and reintroduce the deadlock risk that
§"Transaction boundaries and lock ordering" exists to avoid.

### Bounded work per iteration

`LIMIT` is `min(total idle capacity, MITO_SCHEDULER_MAX_BATCH)`, default 200.
This is what keeps the lock short and the scan bounded: no unbounded query, and
no long-running global lock. A scheduler that cannot finish its work in one tick
finishes it on the next.

### Transaction boundaries and lock ordering

One transaction per batch. Locks are acquired in **one** order — task rows
first, by the `ORDER BY` above, and never a user row — so two schedulers cannot
deadlock by grabbing the same two rows in opposite orders. The capacity read
happens *before* the transaction opens and is re-validated inside it against the
DB constraint, so a stale read cannot over-allocate.

## 5. What is deliberately not adopted

* **No Celery, no distributed queue.** ADR-001's rule applies: adopt
  infrastructure on evidence, not resemblance. A management command invoked by
  cron satisfies the roadmap's gate ("dry-run demos") with no new runtime
  dependency. The algorithm is a pure service function, so if a real scheduler
  is later justified it can call the same function unchanged.
* **No new lease table** — see option 3.
* **No changes to the pull path's semantics.** Push and pull share the same
  eligibility rules (`meets_requirements`) so the two cannot drift; only the
  *batching* differs.

## 6. Conflict with research doc 18, resolved

Doc 18 says: *"Same claim transaction as pull (reuse `assign_instance`
primitive)."* That is option 1, and the measurement in §2 rejects it.

The **intent** behind doc 18's line — that push and pull must not diverge in who
is allowed what — is preserved and is arguably better served here: both paths
call the same `meets_requirements` eligibility predicate, and the batch relies on
the same database constraints as the single claim. What differs is only how many
round trips it takes to apply the same rules.

Per the phase-map gate for Phase 4 ("dry-run demos") and the instruction that a
scheduler performing ~11 round trips per item should not be accepted without
documenting why batching is unsafe, batching is neither unsafe nor unnecessary
here, so it is adopted.

## 7. Acceptance criteria

Phase 4 is complete when, measured and recorded in `PHASE-4-*.md`:

1. queries per batch are **constant in batch size**, not linear;
2. throughput exceeds the looped-`claim_next` baseline by a documented margin;
3. zero duplicate instances, zero counter drift, zero negative `pending_instances`
   under concurrent schedulers;
4. a scheduler killed mid-batch leaves no partial state;
5. replaying a tick's idempotency key assigns nothing new;
6. behaviour with the flag off is byte-identical to Phase 3.
