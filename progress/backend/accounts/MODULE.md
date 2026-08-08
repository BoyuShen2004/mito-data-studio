# `backend/accounts/` — users, roles, institutions, auth

Owns the Django `User` extension (role + institution), the three-role
permission model every other app reads, and the token-auth endpoints the
React SPA uses to log in.

## Files

| File | Purpose |
|---|---|
| `models.py` | `Institution`, `UserProfile` (role + institution + the short self-editable profile: `display_name`, `institution_name`, `contact_note`; one-to-one with `User`), `AnnotatorProfile` (capacity/quality — annotator-only, no pay fields). |
| `roles.py` | `get_role(user)` and the `is_manager`/`is_annotator`/`is_requester`/`can_register_data` predicates every permission check in the codebase ultimately calls. API-level enforcement wraps these in the DRF permission classes in `core/permissions.py`. |
| `api.py` | `LoginView`, `RegisterView`, `LogoutView`, `MeView`, `AnnotatorListView`, and the People surface: `PeopleOverviewView`, `MyProfileView`, `PersonDetailView`. |
| `services.py` | **People** — `people_overview(user)` (role-scoped, one round trip), `person_detail`, `update_own_profile`, and the project↔people helpers (`project_managers`, `project_requester`, `projects_for_annotator`, `annotator_task_counts`). |
| `serializers.py` | `LoginSerializer` (username/password/optional `portal`), `RegisterSerializer`, `CurrentUserSerializer`. |
| `signals.py` | `post_save` on `User` → auto-creates a `UserProfile` (default role `annotator`) so every user always has one. |
| `admin.py` | Registers `Institution`/`UserProfile`/`AnnotatorProfile` on the Manager Admin site (see `core/admin_site.py`). |
| `tests.py` | Role predicate + auth endpoint tests. |
| `test_people.py` | People: the project-centric derivations per role, the profile PATCH allow-list, and the seeded requester accounts. |

## The role model

Three roles used everywhere (`core.choices.UserRole`): `manager`,
`annotator`, `requester` (plus legacy `client`/`reviewer` values kept for
old rows — `is_requester` treats `client` as `requester`, nothing treats
`reviewer` specially).

`get_role(user)`:
1. Superuser → always `manager`, regardless of `UserProfile.role`. This is
   deliberate — it lets an admin-created superuser drive the whole workflow
   without a matching profile row.
2. Otherwise reads `user.profile.role`.

`is_annotator(user)` returns `True` for **both** `annotator` and `manager` —
"annotator" here means "may view annotator-facing pages," and a manager can
view everything. It does **not** mean "may edit any task" — that's a
separate, per-task check (`annotation.services.can_edit_task`), not a role
predicate.

## Auth flow

Token-based, not session/cookie (`rest_framework.authtoken`). `LoginView`
authenticates via Django's standard `authenticate()`, then
`Token.objects.get_or_create(user=user)` and returns `{token, user}`. The
frontend stores the token in `localStorage` and sends
`Authorization: Token <token>` on every request — see
[`../../frontend/api/MODULE.md`](../../frontend/api/MODULE.md).

### Login portals

`LoginView` accepts an optional `portal` field (`"requester"` or
`"annotator"`) and rejects the login (403) if the account's role doesn't
match the tab the user logged in through — e.g. a manager or annotator
account can't log in through the "Institution" tab. This is purely a UX
guard (prevents a confusing landing page), not a security boundary — the
same account/token works identically once issued. See `_portal_allows()` in
`api.py`.

## `AnnotatorListView`

`GET /api/annotators/`, manager-only. Powers the assignment dropdowns
(`AssignmentPlanEditor` on the frontend). Filters to users whose role is
*exactly* `annotator` (not managers, even though `is_annotator()` would
include them) — you assign tasks to annotators, not to yourself-as-manager.

## People — who works with whom (`services.py`)

The collaboration surface behind `/people` and `GET /api/people/overview/`.
The rule that keeps it from becoming three unrelated rosters: **membership is
project-centric**. Nothing in the schema says "annotator X reports to manager
Y"; people are related because they share a project. Every panel is a
projection of that one relation — the same relation
`annotation.services.is_project_member` gates hard cases and task viewing with.

| Role | Panels |
|---|---|
| annotator | manager(s) of the projects they hold tasks on; peer annotators on those projects; their own task counts |
| manager | the annotator roster (workload, submissions made, last decision) **and** the customers/requesters with the projects they registered |
| requester | their projects + which manager owns each, and who is working on them |

`project_managers(project)` reads the project itself (`created_by` when that
user is a manager, plus `reviewed_by`), falling back to *every* manager when a
project has neither — an unreviewed project has no owner yet, and an annotator
asking "who do I hand this to?" deserves an answer rather than an empty list.

The payload shape is **identical for every role** (empty lists where a role has
no such panel) so the frontend renders panels by "is this list non-empty"
rather than branching on role in three places.

`update_own_profile` applies only `EDITABLE_PROFILE_FIELDS` (`display_name`,
`institution_name`, `contact_note`). Role and the institution *link* are
administrative and deliberately unreachable from the self-service endpoint.

Seeded accounts (`core.dev_data.STANDARD_ACCOUNTS`) include **two requesters**
(`requester1`, `requester2`) alongside the manager and four annotators, so the
manager's customer panel has something to show on a fresh database. They also
get `STANDARD_PROFILES` display names/institutions so the roster isn't a row of
blanks.

## Gotchas / things that look like bugs but aren't

- `UserProfile.role` defaults to `annotator` at the model level, but the
  `post_save` signal is what actually creates the row for every new user —
  if you ever bulk-create `User` rows without going through the ORM's normal
  save path (e.g. a raw SQL migration), they won't get a profile and
  `get_role` will return `None` for them until one is created.
- `is_annotator` including managers is intentional (see above) — don't
  "fix" it to be role-exclusive without checking every call site.
- `people_overview`'s manager panel lists annotators who have **no tasks yet**
  too — a manager needs to see who is available, not only who is already busy.
  That's why it unions the "has tasks" query with the role-exact roster.
