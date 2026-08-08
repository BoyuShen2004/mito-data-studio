# Phase 1 — Domain model & permissions

**Status:** complete, shipped inert behind `FEATURE_TEAMS=False`
**Date:** 2026-07-27
**Depends on:** Phase 0 baseline, Phase 0.5 (green suite)
**Gate:** schema approval before the flag is switched on in any real environment

---

## Objective

Give mito the organisational structure its permission model was missing:
teams as the unit of access, experiences as the unit of skill, and an
append-only audit trail — without changing a single access decision on the
day it ships.

## Problem

`Institution` was a weak organisation, there were no teams, and access was
decided by an ad-hoc rule in `annotation.services.is_project_member`:

> the creator, or anyone holding a task on the project

That cannot express "this team may work on this project", cannot gate on
skill, and leaves no record of who granted whom access. Research doc `14`
rates Teams and Experience as **Large** gaps.

## Target behaviour

```
Organization (= Institution)
└── Team
    ├── TeamMembership(role: member | manager)
    └── granted to → Project (M2M)
User
├── Experience(domain, value)
└── UserProfile.role   (unchanged: manager | annotator | requester)
AuditEvent (append-only)
```

Two role axes, deliberately orthogonal — the distinction WEBKNOSSOS draws
between *user* and *team manager*:

| Axis | Field | Values |
|---|---|---|
| Org-wide | `UserProfile.role` | manager / annotator / requester |
| Per-team | `TeamMembership.role` | member / manager |

An annotator org-wide may manage exactly one team. Org managers and superusers
outrank both and need no membership.

## WEBKNOSSOS inspiration

| Concept | Reference | Relationship |
|---|---|---|
| Org → Team → membership | `app/models/{organization,team,user}`, docs `users/teams.html` | independently reimplemented |
| Experience domains | `user_experiences` table, `Experience.scala` | independently reimplemented |
| Default team per org | teams docs | independently reimplemented |

No source copied. Recorded in `attribution/REGISTER.md` under behavioural
references.

## What shipped

| Area | Detail |
|---|---|
| Models | `Team`, `TeamMembership`, `Experience`, `AuditEvent` (`accounts/models.py`) |
| ACL | `Project.teams` M2M (`projects/models.py`) |
| Services | `accounts/teams.py` — membership, project grants, experiences |
| Audit | `accounts/audit.py` — `record_audit`, `audit_trail` |
| Choices | `TeamRole`, `AuditVerb` (`core/choices.py`) |
| Flag | `FEATURE_TEAMS` (`config/settings.py`), default **False** |
| Migrations | `accounts.0004` (schema), `accounts.0005` (backfill), `projects.0006` (M2M) |
| Tests | `accounts/test_teams.py` — 38 tests |

### Integration point

Exactly one existing function changed: `annotation.services.is_project_member`
gained a final fallback.

```python
if project.tasks.filter(assigned_to_id=uid).exists():
    return True

from accounts.teams import has_project_team_access, teams_enabled
return teams_enabled() and has_project_team_access(user, project)
```

Every prior branch is untouched and evaluated first, so team access can only
**widen** the result. `can_view_task`, `can_view_volume`, and the hard-case
surfaces inherit this automatically — they already delegate here.

## Design decisions

**`Institution` *is* the Organization.** Rather than create a parallel
`Organization` table and migrate rows across, teams hang off `Institution` and
the rename is deferred to the contract step. No data moves, no FK is repointed,
and nothing that reads `Institution` today needs to change.

**An empty team set means "fall back to the legacy rule".** A project with no
grants is reachable exactly as before. This is what lets the migration land on
production long before anyone grants a team — the alternative (empty set = deny)
would lock every existing project the moment the flag flipped.

**Audit is best-effort.** `record_audit` catches and logs rather than raising:
a lost log line is bad, a 500 on an otherwise-successful permission grant is
worse. Asserted by test.

**Audit targets are `(type, id)`, not FKs.** Deleting a team must not erase the
record of who was on it. Asserted by test.

## Migrations

All additive. No column altered, no data destroyed.

| Migration | Operation | Reversible |
|---|---|---|
| `accounts.0004` | Create 4 tables + 2 unique constraints | yes (drop) |
| `accounts.0005` | Backfill: default team per org, seat members by role | yes (`remove_default_teams`) |
| `projects.0006` | Create `projects_project_teams` join table | yes (drop) |

`0005` is idempotent and its reverse removes *exactly* what it created —
hand-made teams survive a rollback. Both properties are tested.

**Not yet applied to any database.** `makemigrations --check` is clean and
`migrate --plan` resolves; running `migrate` is a schema decision awaiting
approval.

## Tests — 38

| Group | Covers |
|---|---|
| `TeamMembershipTests` | add/remove/promote, idempotency, per-org name uniqueness, default team |
| `TeamRoleTests` | org role vs team role orthogonality, anonymous users |
| `ProjectTeamAccessTests` | grant/revoke, "no grant is not a grant" |
| `FeatureFlagTests` | **flag off ⇒ behaviour identical to pre-Phase-1** |
| `PermissionMatrixTests` | the 4-role acceptance matrix |
| `ExperienceTests` | set/clear/update, zero-requirement semantics |
| `AuditTests` | actor capture, previous-role, survives target deletion, failure tolerance |
| `BackfillMigrationTests` | forward, idempotent, exact reverse, bespoke teams preserved |

### Permission matrix (acceptance criterion)

With `FEATURE_TEAMS=True`, project granted to team T, project created by the requester:

| | org manager | team manager (T) | annotator (T) | requester (creator) | outsider |
|---|---|---|---|---|---|
| `is_project_member` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `is_team_manager(T)` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `is_team_member(T)` | ❌ (not needed) | ✅ | ✅ | ❌ | ❌ |

## Rollback

1. `FEATURE_TEAMS=False` — instant, no deploy needed. All team logic goes inert.
2. `python manage.py migrate accounts 0003 && python manage.py migrate projects 0005`
   — drops the tables and unseats the backfill.

Step 1 alone is sufficient to neutralise Phase 1; step 2 is only for removing
the schema.

## Not in this phase

- No API endpoints or UI for team management (Phase 6 surfaces them)
- `Experience` is written and read but not yet *enforced* — Phase 3 uses
  `meets_experience` for claim eligibility
- `Institution` → `Organization` rename (contract step, later)
- Dataset-level ACL (`16` lists `DatasetACL`; projects were the pressing gap)
