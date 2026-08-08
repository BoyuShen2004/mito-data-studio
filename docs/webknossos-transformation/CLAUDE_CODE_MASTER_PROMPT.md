# CLAUDE CODE MASTER PROMPT — Rebuild mito-data-agent to WEBKNOSSOS-Level Quality

> **This document is the executable project instruction for Claude Code.**
> It was produced by Cursor after source-level research of WEBKNOSSOS and mito-data-agent.
> Claude Code must re-verify every cited path against a local clone before editing production code.

---

## A. Mission

Transform `/home/weidf/shenb/mito-data-agent` into a **production-grade** mitochondria-focused annotation, inference, QC, and crowdsourcing platform whose **engineering maturity matches WEBKNOSSOS**.

### Quality north star

> Make an implementation as mature, stable, and complete as WEBKNOSSOS — not a thin imitation, not a small patch set.

Comparable means: task system, concurrency safety, annotation durability, chunked multiresolution IO, responsive navigation, soak stability, permissions, observability, tests, and operable deployments.

Preserve mito advantages: mitochondria workflows, HPC indexing, EfficientSAM/SAM2, hard cases, submit/review/revise/lock, Slurm path, prediction/QC lineage.

### Scope authority

Large refactors are **in scope**. Service extraction, Postgres migration, datastore introduction, frontend data-path redesign, and AGPL-compliant reuse/adaptation of WEBKNOSSOS code are allowed. Inferior local designs must not be preserved merely to shrink diffs.

The user will comply with WEBKNOSSOS licenses. You must still document licenses, attribution, copied/modified files, and distribution obligations. Never copy code silently.

---

## B. Source locations

| Resource | Path / URL |
|---|---|
| App to change | `/home/weidf/shenb/mito-data-agent` |
| Local WK Python libs | `/home/weidf/shenb/webknossos-libs` (official `webknossos-libs`, **not** the main app) |
| Research pack | `/home/weidf/shenb/mito-data-agent/docs/webknossos-transformation/` |
| Main WK clone target | `/home/weidf/shenb/external-research/webknossos` |
| Upstream main | https://github.com/scalableminds/webknossos (AGPL-3.0) |
| Upstream libs | https://github.com/scalableminds/webknossos-libs |
| Docs | https://docs.webknossos.org/ |

**Verified at research time:** local `webknossos-libs` HEAD `0419d102`; WK latest release tag `26.08.0`. Re-check.

---

## C. Mandatory initial audit

1. Read all files in `docs/webknossos-transformation/` (`00`–`27`, `CLAUDE_CODE_MASTER_PROMPT.md`, `evidence/source-map.md`).
2. Clone main WK if missing (outside mito repo).
3. Diff Cursor claims vs current code; write `evidence/AUDIT_DELTA.md`.
4. Confirm licenses.
5. Only then start Phase 0.

---

## D. Safety requirements

Before structural work:

- Clean understanding of `git status` (there may already be unrelated WIP — do not discard user work).
- DB backup; label-store backup plan.
- Baseline tests + benchmarks recorded.
- Feature flags; migration dry-runs; rollback notes.
- **No** prod migrations, prod restarts, Cloudflare edits, data deletion, or pushes unless the user explicitly orders them.

---

## E. Phases (summary)

| # | Phase |
|---|---|
| 0 | Baseline, backups, tests, benchmarks |
| 1 | Domain model & permissions |
| 2 | WK-style task hierarchy |
| 3 | Concurrency-safe assignment engine |
| 4 | Auto-fill scheduler |
| 5 | Review / rejection / resubmission |
| 6 | Dashboards & statistics |
| 7 | Annotation operation model |
| 8 | Interpolation |
| 9 | Additional annotation tools |
| 10 | Autosave, undo/redo, recovery |
| 11 | Volume storage & pyramids |
| 12 | Dedicated chunk/datastore service |
| 13 | Frontend chunk cache & request scheduler |
| 14 | Rendering & navigation redesign |
| 15 | Mesh & large-label scalability |
| 16 | Hard-case sharing & deep links |
| 17 | AI inference & SAM endpoints |
| 18 | HPC & Slurm integration |
| 19 | Observability |
| 20 | Load & soak tests |
| 21 | Production migration |
| 22 | License & attribution verification |

Detailed requirements per phase follow.

---

## E0. Phase 0 — Baseline

**Objective:** Know current truth before changing it.

**Work:**

- Document env (SQLite, data root, GPU).
- `python manage.py test` full suite; capture failures.
- Micro-benchmarks on a large volume: TTFV, scrub p50/p95, paint latency, RSS, optional multi-tab.
- Clone WK; open `Task.scala`, `volume_interpolation_saga.ts`, `pullqueue.ts`.
- Write `benchmarks/BASELINE.md`.

**Acceptance:** Baseline numbers + AUDIT_DELTA exist; user approves architecture direction.

**Stop:** architecture approval gate.

---

## E1. Phase 1 — Domain model & permissions

**Problem:** Institution ≠ Organization; no Teams; ACL helpers are ad hoc.

**Target:**

```
Organization → Team → Membership(role)
User → Experiences[]
Dataset/Project ACL via team
AuditEvent
```

**Approach:** expand-contract migrations from `Institution`/`UserProfile`.

**Inspiration:** WK org/team/user docs + `app/models/{organization,team,user}`.

**Tests:** permission matrix for manager/team-manager/annotator/requester.

**Flag:** `FEATURE_TEAMS`.

**Rollback:** reverse migration; flag off.

---

## E2. Phase 2 — Task hierarchy

**Problem:** `AnnotationTask` conflates task definition, instance, and assignment.

**Target entities:** `TaskType`, `Project`(priority,paused,team), `Task`, `TaskInstance`/`Assignment` with `total_instances`/`pending_instances`.

**Backfill:** each existing AnnotationTask → Task + 1 instance; TaskType from enum; preserve FKs to submissions/hard cases.

**Inspiration:** `docs/tasks_projects/concepts.md`, `TaskType.scala`, `Project.scala`, `Task.scala`.

**API slices:** CRUD TaskType → Project fields → Task create/bulk → admin UI.

**Flag:** `FEATURE_TASK_HIERARCHY`.

**Acceptance:** old UI still works via compatibility shims.

---

## E3. Phase 3 — Assignment engine

**Problem:** push-only assignment; SQLite; no atomic multi-instance claims.

**Target pull flow:**

```
POST /api/tasks/claim-next/
  → resolve eligible teams/experiences/capacity
  → atomically claim instance (pending_instances--)
  → create AnnotationSession
  → return deep link to editor
```

**Concurrency strategy (pick one, test hard):**

1. **WK-like:** Serializable transaction inserting assignment row that exists only if eligibility query returns a row; DB trigger/constraint on pending; retry on serialization failure (see `assignNext`).
2. **Queue-like:** `SELECT id FROM task_instances WHERE state='pending' AND … FOR UPDATE SKIP LOCKED LIMIT 1` then update.

Must prevent: double claim, claim after cancel, claim without capacity, progress drift.

**Also:** manual assign, transfer, peek-next, max-active enforcement (see WK `getAllowedTeamsForNextTask` / `maxOpenPerUser`).

**DB:** Postgres required for real concurrency tests.

**Inspiration:** `TaskController.request`, `Task.scala` `findNextTaskQ`/`assignNext`, evolution `008-task-instances-triggers.sql`.

**Tests:** 20 concurrent claim workers on 1 remaining instance → exactly one winner.

**Flag:** `FEATURE_TASK_CLAIM`.

---

## E4. Phase 4 — Auto-fill scheduler

**Beyond WK.** Modes: pull (already), **push auto-fill**, **hybrid** (recommendations).

**Available user:** enabled, team-eligible, under capacity, not paused/leave, experience OK, recently active — configurable.

**Deterministic score** with weights; write `SchedulerDecision` audit; dry-run; metrics; fairness tests.

**Never** invent slang like “empty people” in code/UI — use `available` / `idle_capacity`.

**Flag:** `FEATURE_AUTO_FILL`.

---

## E5. Phase 5 — Review loop

**Retain mito semantics** (`annotation_locked`, approve/reject/revision). Improve history to append-only submissions. Immutable reviews. Transition table tests.

---

## E6. Phase 6 — Dashboards

Instance bar charts; time tracking; CSV; integrate People pages. Inspiration: WK project progress + time tracking docs.

---

## E7. Phase 7 — Annotation operations

Introduce server-ack **op log** + sparse chunk versions (see `22-target-persistence-and-recovery.md`). Feature-flag beside current PUT label-ids. Migration path from working TIFF memmap.

**Flag:** `FEATURE_ANNOTATION_OPS`.

---

## E8. Phase 8 — Interpolation (P0)

**Match WK algorithm, improve UX with preview.**

Algorithm reference (`volume_interpolation_saga.ts`):

1. Active segment masks on endpoints.
2. Signed distance transform each.
3. Linear blend distances along axis; label where result < 0.
4. Max depth 100; min depth 2; anisotropic mag-aware spacing.
5. Overwrite policy respected.
6. Single undoable transaction after confirm.

**mito UX:** annotate A → annotate B → Interpolate → **preview** → confirm/cancel.

**Tests:** synthetic cylinders/holes/topology change golden masks; anisotropy case.

**License:** prefer independent reimplementation of SDF method with citation; if copying saga code → AGPL register.

**Flag:** `FEATURE_INTERPOLATION`.

---

## E9. Phase 9 — More tools

Implement P1 from `19-target-annotation-design.md`. Keep EfficientSAM/SAM2. Do not remove good mito tools to mirror WK.

---

## E10. Phase 10 — Autosave / undo / recovery

Autosave batches; IndexedDB draft optional; refresh recovery; save chrome indicator; soak kill-tab tests.

---

## E11–E13. Volume IO stack

Follow `20` + `21` docs:

- Pyramid Zarr3 derivatives (Phase 11).
- Chunk service with token auth (Phase 12).
- Frontend PullQueue-like manager (Phase 13) inspired by WK `pullqueue.ts`.

**Acceptance scrubbing:** p95 slice change after warmup meets targets in `21-target-rendering-architecture.md` (or revised post-baseline).

---

## E14–E15. Rendering & meshes

Preserve Canvas proofreading UI; upgrade data path; optional WebGL overlay; mesh disposal/LOD.

---

## E16. Deep links & hard cases

Extend share tokens with camera/slice/label/layers; security test public routes remain read-only.

---

## E17–E18. Inference & Slurm

Wire `ProcessingJob` for real nnU-Net predict; versioned outputs; auto task spawn; finish Slurm adapter; optional MIT `cluster_tools`.

---

## E19–E22. Ops maturity

Observability (`23`), load/soak (`25`), prod migration (`24`), license (`26` + Phase 22).

---

## F. Per-phase checklist template

Copy this into each phase PR description:

```
Objective:
Problem:
Target behavior:
WK inspiration paths:
Reuse decision:
Backend/DB/FE/Service/API:
Migrations:
Tests:
Benchmarks:
Feature flag:
Deploy:
Rollback:
Acceptance:
Docs:
License records:
```

---

## G. Vertical slices

Ship reviewable thin slices. Example claim API: migration → service `claim_next_task` → API → AnnotatorDashboard button → race tests → docs.

---

## H. Authority

Allowed: redesign apps, add Postgres/Redis/workers/chunk service/Web Workers, Zarr, feature flags, AGPL-compliant ports, delete fragile code paths behind flags.

Not allowed: silent full-program execution past gates; destroying user WIP; skipping attribution.

---

## I. Preservation

Data, users, annotations, familiar proofreading UX, hard cases, review semantics, SAM tools, HPC registration heuristics, rollback.

---

## J. Gates

```
Research (done by Cursor)
 → your audit + Phase 0 baseline
 → architecture approval (USER)
 → schema approval (USER)
 → task-management rollout (USER)
 → annotation rollout (USER)
 → volume-infrastructure rollout (USER)
 → production migration (USER)
```

---

## K. Definition of done

All boxes in research pack §K / Master Prompt section K (concurrency-safe claims, complete workflows, interpolation, durable ops, chunked IO, responsive scrubbing, soak memory, multi-user, permissions, reviews, migrations, monitoring, rollback, load/soak, licenses, familiar UI).

---

## L. Default reuse matrix

| Area | Decision |
|---|---|
| Task hierarchy | Reimplement behavior in Django |
| Claim locking | Reimplement WK strategy |
| Auto-fill | New |
| Review/QC/hard-case | Retain mito |
| Interpolation | Port algorithm (SDF) |
| Chunk pull scheduling | Port concepts from PullQueue |
| Datastore | New Python service; optional WK AGPL datastore later |
| Zarr tooling | TensorStore preferred; `webknossos` package only if AGPL accepted |
| cluster_tools | MIT reuse OK |
| Proofreading UI | Retain |
| SAM | Retain |

---

## M. Immediate Phase 0 commands

```bash
cd /home/weidf/shenb/mito-data-agent
git status -sb
mkdir -p /home/weidf/shenb/external-research
test -d /home/weidf/shenb/external-research/webknossos || \
  git clone --depth 1 https://github.com/scalableminds/webknossos.git \
    /home/weidf/shenb/external-research/webknossos
cd backend && python manage.py test 2>&1 | tee ../docs/webknossos-transformation/benchmarks/TEST_BASELINE.txt
```

Then produce `benchmarks/BASELINE.md` and **stop for user architecture approval**.

---

## N. Research pack map

| Doc | Content |
|---|---|
| 00 | Repo verification |
| 01 | Source index |
| 02 | Licenses |
| 03–11 | WK deep dives |
| 12–13 | mito audits |
| 14 | Gap matrix |
| 15–23 | Target designs |
| 24–26 | Rollout / tests / compliance |
| 27 | Phase map |
| evidence/source-map.md | Citation index |

---

**END.** Quality bar: WEBKNOSSOS-level maturity for mito-data-agent — execute phase by phase behind gates.
