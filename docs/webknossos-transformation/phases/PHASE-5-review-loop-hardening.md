# Phase 5 — Review loop hardening

**Status:** complete, shipped inert behind `FEATURE_REVIEW_HISTORY`
**Date:** 2026-07-28
**Depends on:** Phase 2 (per phase map), PostgreSQL
**Gate (phase map):** parity with current UX
**Design record:** [ADR-003](../adr/ADR-003-review-loop-hardening.md)

---

## Objective

Master prompt §E5: *"Retain mito semantics (`annotation_locked`,
approve/reject/revision). Improve history to append-only submissions. Immutable
reviews. Transition table tests."*

Three defects in the existing loop, all confirmed by reading the code rather
than inferred:

1. **History was destroyed on resubmit.** `_supersede_submissions` deleted every
   prior `AnnotationSubmission` and unlinked its file. A rejected round left no
   record of what had actually been rejected.
2. **Reviews were editable.** `ReviewRecordAdmin` blocked add/edit in the admin
   UI, and nothing below it did — the shell, a script, or any future API could
   silently rewrite a recorded verdict.
3. **No transition rules existed.** Any status could become any other. Nothing
   stopped an unsubmitted task being approved.

## What shipped

| Area | Detail |
|---|---|
| Model | `AnnotationSubmission.{superseded_at, supersedes}` + `idx_submission_current`; `ReviewRecord.save()` immutability |
| Service | `annotation/transitions.py` (new); `services.{review_history_enabled, _retire_submissions, _lock_task_for_submit, current_submission, submission_history, _set_task_status}` |
| Errors | `annotation/review_errors.py` — own module to avoid a models↔services import cycle |
| Audit | `SUBMISSION_CREATED`, `SUBMISSION_SUPERSEDED`, `REVIEW_RECORDED` verbs; review decisions audited |
| Migrations | `annotation.0011_submission_history` (additive), `accounts.0007_alter_auditevent_verb_phase5` (state-only, no DDL) |
| Flag | `FEATURE_REVIEW_HISTORY`, default **False** |
| Tests | `annotation/test_review_loop.py` — **57 tests** |
| Benchmark | `benchmarks/bench_review_history.py` |

**No new API endpoint and no UI.** Phase 5's written scope is the data model and
service semantics; manager-facing surfaces are Phase 6.

## Conflicts resolved

Recorded in full in ADR-003 §2; summarised because both changed the shape of the
implementation.

**A — "append-only" versus a deliberate deletion rule.** The existing docstring
states latest-wins deletion is *"a product rule, not an accident"*. The master
prompt requires append-only history. Resolved by separating storage from
semantics: **storage becomes append-only, reads stay latest-wins**. A reviewer
sees exactly what they saw before, which is what preserves the phase gate.

The stated concern is not dismissed — with the flag on, uploaded files are
retained, so submission storage grows per review round. Keeping the file
alongside the row is deliberate: a history row pointing at a deleted file is
worse than no history, because it looks retrievable and is not.

**B — "DB constraints" for a transition.** A `CHECK` sees only the row being
written, never the row it replaces, so it cannot express *"submitted may become
approved"*; only a trigger can. Doc 16 says "where feasible", and a trigger was
judged not feasible here: triggers are invisible to Django's migration state and
fire during `loaddata` — a failure mode this repository has already been bitten
by once (`ensure_user_profile`, fixed in `fc0e7aa`). Enforcement is service-layer
with the table declared in one place. Documented as a deliberate partial
implementation, not an oversight.

**C — immutability versus dev reset.** `core/dev_data.py` deletes review rows.
Immutability blocks **updates**, not deletes.

## Two bugs the tests caught

Both were found by tests failing, not by review, and both are genuine.

**1. Concurrent resubmits produced several "current" submissions.**
Each transaction marked "everything except mine" superseded against its own
snapshot, and never saw the rows the others were creating.

```
test_concurrent_resubmits_leave_one_current
  AssertionError: 4 != 1 : expected exactly one current submission, got 4
```

Fixed with `_lock_task_for_submit`: both submit paths take
`SELECT … FOR UPDATE` on the task row at the start of the transaction, before
the previous round is read. Only the task row is locked, matching the ordering
the claim and scheduler paths already use — no new lock class, so no new
deadlock surface.

**2. The transition check read a stale in-memory status.**
`_set_task_status` validated `task.status` from the Python instance, which a
caller may have loaded long before. A task moved in the database behind the
service's back still validated against the old value and wrote anyway — exactly
the class of bug a transition table exists to prevent. It now re-reads the
status from the locked row.

## Measured behaviour

`bench_review_history.py`, PostgreSQL 16, query counts via
`CaptureQueriesContext` and PostgreSQL-reported execution time.

### Superseding N prior submissions

| History depth | Strategy | SQL queries | Wall | DB time |
|---|---|---|---|---|
| 1 | delete (baseline) | 6 | 5.14 ms | 2 ms |
| 1 | **append-only** | **1** | **1.91 ms** | 1 ms |
| 5 | delete | 26 | 28.22 ms | 12 ms |
| 5 | **append-only** | **1** | **3.02 ms** | 2 ms |
| 20 | delete | 101 | 60.26 ms | 22 ms |
| 20 | **append-only** | **1** | **2.18 ms** | 2 ms |
| 100 | delete | 501 | 385.38 ms | 151 ms |
| 100 | **append-only** | **1** | **3.10 ms** | 3 ms |

The deletion path is a Python loop issuing ~5 queries per row; append-only is a
single `UPDATE` regardless of depth. At depth 100 that is **501× fewer queries
and 124× faster**.

Worth stating plainly: **append-only is not a cost paid for auditability here —
it is strictly cheaper than the deletion it replaces.** The trade is disk, not
time.

### Reading the current submission

| History depth | SQL | p50 | max |
|---|---|---|---|
| 1 | 1 | 1.35 ms | 1.54 ms |
| 5 | 1 | 2.52 ms | 2.75 ms |
| 20 | 1 | 1.40 ms | 1.48 ms |
| 100 | 1 | 1.15 ms | 1.31 ms |

Flat. One indexed query whether a task has one round or a hundred, which is what
`idx_submission_current` exists for. Had this degraded, append-only history
would have traded a real regression for an audit trail.

The benchmark asserts its own correctness invariants — no row lost, exactly one
current submission — and exits non-zero if either fails.

## Compatibility

`FEATURE_REVIEW_HISTORY` deliberately does **not** require
`FEATURE_TASK_HIERARCHY`. The review loop predates the hierarchy and runs on the
legacy single-assignee path, which is the deployment most likely to want the
history fix first. Smoke configuration **F** exists to prove that claim.

With the flag off:

- resubmitting deletes the previous submission, exactly as before;
- `supersedes` is not populated and nothing is marked superseded;
- an illegal transition is logged and **permitted**, so deploying the table
  cannot break a deployment whose historical data disagrees with it.

Immutability is **not** flag-gated. A recorded verdict is never editable, in any
configuration — there is no deployment in which rewriting one is correct.

`can_submit_task` / `can_annotate_task` are untouched and still key off
`annotation_locked` alone. **No permission is narrowed by this phase.**

## Known gaps, stated rather than implied

- `ReviewRecord.objects.filter(...).update(...)` bypasses `save()` and is **not**
  blocked. Django offers no hook; only a trigger would close it, which ADR-003
  declines. `test_queryset_update_is_a_known_gap` pins the current behaviour so
  nobody assumes protection that does not exist.
- Uploaded-file submissions still never merge back into `Volume.label_path` —
  a long-standing out-of-scope note in `approve_submission`, unchanged here.
- Storage grows with review rounds when the flag is on. Bounded per round, not
  per read, but it is real and there is no pruning job.

## Not in this phase

- Manager dashboards, review queues, statistics → Phase 6.
- Scheduler HTTP controls or frontend UI → Phase 6 (explicitly excluded).
- `apply_plan` remains unwired — Phase 5 does not require it.
- No change to `FEATURE_AUTO_FILL_SCHEDULER`'s default, and no redesign of the
  scheduler's advisory-lock serialisation.
