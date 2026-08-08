# ADR-003 — Review loop hardening: append-only history, immutable reviews, explicit transitions

**Status:** accepted, 2026-07-28
**Phase:** 5 (review / reject / resubmit hardening)
**Depends on:** Phase 2 (per phase map), PostgreSQL
**Gate (phase map):** parity with current UX
**Related:** ADR-001 (evidence before rewrites), ADR-002 (batching on measurement)

---

## 1. Authoritative scope

Assembled from the written specification, not from a recap.

| Source | Says |
|---|---|
| `CLAUDE_CODE_MASTER_PROMPT.md` §E5 | "Retain mito semantics (`annotation_locked`, approve/reject/revision). Improve history to **append-only submissions**. **Immutable reviews**. **Transition table tests**." |
| `27-claude-code-phase-map.md` row 5 | "Review/reject/resubmit hardening", depends on Phase 2, gate **"parity with current UX"** |
| `16-target-domain-model.md` | "Submission (keep mito review richness; prefer append-only history)", "Review / Revision (**immutable**)"; assignment state machine; "Use Postgres CHECK/ENUM + explicit transition table; forbid illegal jumps in service layer **and** DB constraints where feasible" |
| `14-complete-feature-gap-matrix.md` | Review loop: "Retain & extend", owner "mito lead" |
| `15-target-product-definition.md` | "Submit/review/revise/lock semantics" listed under what must be **preserved** |

### Required

1. **Append-only submission history** — resubmitting must stop destroying the
   previous submission.
2. **Immutable reviews** — a `ReviewRecord` must not be editable after it is
   written, enforced below the admin layer.
3. **Explicit transition table** — legal task-status transitions declared in one
   place and enforced in the service layer, with database enforcement *where
   feasible*.
4. **Retain mito semantics** — `annotation_locked`, and the
   approve / reject / revision verdicts, behave exactly as today.
5. **Transition tests.**

### Excluded (belongs elsewhere, or explicitly out of bounds)

- Dashboards, statistics, manager screens → Phase 6.
- Scheduler HTTP endpoints / frontend UI → Phase 6.
- `apply_plan` wiring — Phase 5 does not require it, so it stays unwired.
- Any change to `FEATURE_AUTO_FILL_SCHEDULER`'s default.
- Per-user scheduler locking redesign — Phase 5 does not require it.
- Uploaded-file → volume label merging (long-standing out-of-scope note in
  `approve_submission`).

### Affected

| Kind | Item |
|---|---|
| Models | `AnnotationSubmission` (+`superseded_at`, +`supersedes`), `ReviewRecord` (immutability) |
| Services | `annotation/transitions.py` (new), `annotation/services.py` (`_supersede_submissions`, `_record_review`, submit/approve/reject/revision) |
| Migration | `annotation/0011_submission_history` — **additive only** |
| Flag | `FEATURE_REVIEW_HISTORY`, default **False** |
| API | None new. Serializers gain read-only history fields. |
| Background | None. |

## 2. Conflicts found, and how they were resolved

### Conflict A — "append-only" vs a deliberate deletion rule

`services._supersede_submissions` **deletes** every prior submission on resubmit,
and its docstring states this is intentional:

> "Latest-submission-wins is a product rule, not an accident: the manager reviews
> the newest state of the work, and old in-app checkpoints/uploads are dead
> weight on disk."

The master prompt requires append-only history. These are in direct conflict.

**Resolution.** The master prompt is the most recent explicit decision record and
governs. The *least destructive* reading, which satisfies both documents:

- **Storage becomes append-only** — prior submissions are marked
  `superseded_at` instead of being deleted.
- **Semantics stay latest-wins** — every read path still surfaces the current
  submission, so the manager reviews the newest state exactly as before. This is
  what preserves the phase gate, "parity with current UX".

The stated concern (disk) is real and is not dismissed: with the flag on,
uploaded files are retained rather than unlinked, so submission storage grows
with review rounds instead of staying at one file per task. In-app submissions
own no file at all (they point at the volume's shared working copy), so they add
rows only. This is documented as an operational consequence of enabling the
flag, not hidden.

Retaining the file alongside the row is deliberate: a history row pointing at a
deleted file is worse than no history, because it looks retrievable and is not.

### Conflict B — "DB constraints" for a transition

Doc 16 asks for transitions enforced by "Postgres CHECK/ENUM + explicit
transition table … in the service layer **and** DB constraints where feasible."

A `CHECK` constraint cannot express a *transition*: it sees only the row being
written, never the row it replaces. Enforcing `submitted → approved` in the
database requires a trigger comparing `OLD` and `NEW`.

**Resolution — honour "where feasible" literally.** The transition table is
declared once and enforced in the service layer. The database enforces what it
can express about a single row: `status` is already constrained to the
`TaskStatus` choices, and the new history columns carry their own constraints. A
trigger is **not** added:

- ADR-001's rule — adopt mechanism on evidence, not on resemblance. There is no
  measured incident of an illegal transition bypassing the service layer.
- Triggers are invisible to Django's migration state and to developers reading
  models, and they would fire during `loaddata`, breaking fixture restore — a
  failure mode this repository has already been bitten by once
  (`ensure_user_profile`, fixed in `fc0e7aa`).
- Every write path already funnels through the service layer.

Recorded as a deliberate partial implementation rather than an oversight. If a
transition is ever observed bypassing the services, a trigger becomes justified
and this decision should be revisited.

### Conflict C — immutability vs the existing dev-reset path

`core/dev_data.py` deletes `ReviewRecord` rows wholesale when clearing
development data. Blanket immutability would break `manage.py` dev reset.

**Resolution.** Immutability blocks **updates**, not deletes. An append-only log
that cannot be edited is what "immutable review" means here; wiping a
development database is an administrative action outside that guarantee, and
`ReviewRecordAdmin` already blocks deletion for managers while allowing it for
superusers.

## 3. Decision

### Append-only submissions

`AnnotationSubmission` gains:

- `superseded_at` (nullable timestamp) — set when a newer submission replaces it;
- `supersedes` (self-FK, `SET_NULL`) — the chain, so a review round is
  reconstructable without relying on timestamps alone.

With `FEATURE_REVIEW_HISTORY` **off**, `_supersede_submissions` deletes exactly
as it does today. With it **on**, it marks instead. One flag, one behavioural
switch, and the flag is the only thing that decides which.

`AnnotationTask.submission_count` keeps counting rounds and is unchanged.

### Immutable reviews

Enforced in `ReviewRecord.save()` — the one place every write passes through,
including the admin, the shell and a future API. A saved record raises
`ImmutableReviewError` on any subsequent save.

> This is the one place business logic is deliberately placed in a model method
> rather than a service. The rule is *"this row cannot change"*, which is a
> property of the row itself; putting it in a service would leave
> `ReviewRecord.objects.filter(...).update(...)` and shell access unguarded. The
> service layer still owns everything about *what a review means*.

`.update()` on a queryset bypasses `save()` and is not interceptable; that gap
is documented and tested for explicitly so nobody assumes protection that is not
there.

### Explicit transition table

`annotation/transitions.py` declares the legal task-status graph in one dict,
derived from doc 16's assignment state machine mapped onto mito's `TaskStatus`:

```
unassigned  -> assigned, in_progress
assigned    -> in_progress, submitted, unassigned
in_progress -> submitted, unassigned, assigned
submitted   -> approved, rejected, revision_requested, in_progress
approved    -> in_progress, submitted        (reopened by an unlock)
rejected    -> in_progress, submitted, unassigned
revision_requested -> in_progress, submitted, unassigned
```

Self-transitions are always legal (idempotent re-save). The service layer calls
`assert_transition(old, new)` before writing.

With the flag **off**, `assert_transition` logs and permits — so an existing
deployment with historically odd data cannot be broken by turning the code on.
With it **on**, an illegal transition raises. This is the expand-contract
posture used in every prior phase: ship inert, observe, then enforce.

## 4. Boundaries

| Concern | Decision |
|---|---|
| Transaction | One per review decision: record + task update, atomic. Superseding is inside the submit transaction. |
| Lock ordering | **Task row only**, in two places — at the head of a submit transaction (`_lock_task_for_submit`) and around a status change (`_set_task_status`). Matches the ordering the claim and scheduler paths already use, so no new lock class and no new deadlock surface. |
| Idempotency | Re-approving an already-approved submission is a legal self-transition and writes a second review row — the log is append-only by design. Superseding is idempotent: an already-superseded row keeps its original timestamp. |
| Retry / crash | No multi-step external work; a failed review rolls back whole. No reconciliation sweep needed. |
| Permissions | Unchanged. `can_submit_task` / `can_annotate_task` keep keying off `annotation_locked` alone. **No permission is narrowed.** |
| Observability | Audit events for approve / reject / revision, via the existing best-effort `record_audit`. |
| Rollback | Flag off restores current behaviour exactly. The migration is additive, so rolling the *code* back leaves two unused nullable columns rather than an error. |

## 5. Compatibility matrix

Phase 5 touches submissions and reviews, which are independent of teams, task
hierarchy, claiming and scheduling. It must therefore work in every
combination:

| Flags | Expected |
|---|---|
| all off | today's behaviour, byte-for-byte |
| `FEATURE_REVIEW_HISTORY` alone | append-only + immutability + transitions, no hierarchy needed |
| with Phases 1–4 on | unchanged; a claimed or scheduled task reviews identically |

Unlike `FEATURE_TASK_CLAIM` and `FEATURE_AUTO_FILL_SCHEDULER`, this flag does
**not** require `FEATURE_TASK_HIERARCHY`: the review loop predates the hierarchy
and works on the legacy single-assignee path, which is exactly the deployment
most likely to want the history fix first.

## 6. Acceptance criteria

1. Resubmission preserves prior submissions with the flag on, and deletes them
   with the flag off.
2. A `ReviewRecord` cannot be updated after creation.
3. Every legal transition in the table is permitted; every illegal one is
   rejected with the flag on, and permitted-with-a-warning with it off.
4. `can_submit_task` / `can_annotate_task` behaviour is unchanged in all
   configurations — the parity gate.
5. Migration is additive and reverses cleanly; a fresh database and an existing
   one converge.
6. History growth is bounded per review round, and reading "the current
   submission" stays a single indexed query — no scan proportional to history.
