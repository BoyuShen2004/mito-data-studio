# Manager Admin (Django Admin)

> Part of [progress/](README.md). To extend admin screens/actions, see
> [codemap.md](codemap.md#manager-admin-the-managers-portal).

Django Admin is the **primary operational interface for the Manager role**. It is
no longer a debugging-only screen: a manager runs their full daily workflow here,
while the React SPA continues to serve **annotators** and **requesters**.

The admin is served at `/admin/` by a custom site,
`core.admin_site.ManagerAdminSite` (installed via
`core.admin_apps.ManagerAdminConfig` in `INSTALLED_APPS`, replacing the default
`django.contrib.admin` site). It is branded **“Mito Data Agent Manager”**.

## Roles and access

| Role | Where they work | Admin access |
| --- | --- | --- |
| **requester** | React SPA | none |
| **annotator** | React SPA | none |
| **manager** | **Manager Admin** (+ can log into the SPA) | yes, if `is_staff=True` |
| **superuser** | everything | full, including Users/Groups/Tokens |

Access rules (enforced by `ManagerAdminSite.has_permission` +
`core.admin_common.ManagerAdminAccessMixin`, both keyed off the existing
`accounts.roles.is_manager`):

- A user may open the Manager Admin only if they are **active**, **`is_staff`**,
  and have the **manager role** (superusers qualify automatically).
- Annotators and requesters (never `is_staff`) cannot access it.
- Managers only see the management models (projects, volumes, tasks,
  submissions, reviews, institutions, user/annotator profiles). Django's
  `User`, `Group`, and auth-token admins remain **superuser-only** (managers
  have no model permissions for them), so password hashes, tokens, and the
  `is_superuser` flag are never exposed to managers.

### Granting / revoking manager admin access

Only a **superuser** can grant admin access:

1. Create the user (they can self-register in the SPA, or be created in the
   `User` admin).
2. Set the user's app role to **manager** — in the Manager Admin open
   **Accounts → User profiles**, edit the profile, set **role = manager**. (The
   `role` field is editable only by superusers, so managers cannot mint other
   managers.)
3. Set **`is_staff = True`** on the `User` (Users admin, superuser-only).

Revoke by unsetting `is_staff` or changing the role away from manager.

### Create the initial superuser

```bash
cd backend
python manage.py createsuperuser
```

A superuser is a manager for all admin purposes and also retains Django's
built-in user/group/token management.

## What managers can do in the admin

**Dashboard** (`/admin/`) — a **Data lifecycle** row (New / To Proofread / Done
project counts, each linking to the lifecycle-filtered Project changelist) plus
the **Workflow** metrics, each linking to the matching filtered changelist:
projects awaiting approval, approved projects, unassigned tasks,
assigned/in-progress tasks, submissions awaiting review, revision-requested
tasks, overdue tasks, active annotators, annotators at capacity.

**Projects** — list with **lifecycle** (New/To Proofread/Done), **workflow
type**, approval state, deadlines, volume/task/approved counts and progress%.
Filters include a **lifecycle** filter and **workflow type**. Actions: **Approve
selected projects**, **Auto-assign tasks for selected approved projects** (one
whole-volume task per volume, balanced across active annotators), **Show
progress**. Links to a project's volumes and tasks.

**Processing jobs** — monitor HPC/local jobs: type, backend, status, linked
project/volume/task, external job id, retry count, and read-only config /
input-paths / output-paths. Actions: **Retry** (terminal jobs), **Cancel**, and
**Run selected queued jobs now** (dev). Jobs are created by the service layer,
not by hand; all actions call `processing.services`.

**Volumes** — file/label paths, label type, format, derived shape (read-only),
and task counts. Actions: **Split into frame-based tasks** (default z-step) and
**Create one whole-volume task**. Both require an **approved** project, skip
volumes without a detectable shape, and never duplicate tasks — ineligible
volumes are reported per-object.

**Annotation tasks** — the task operations screen. Filters for status, type,
project, annotator, priority, difficulty, deadline, plus **active** and
**overdue** filters. Actions: **Assign to an annotator** (intermediate form;
respects `max_active_tasks` and reports over-capacity), **Unassign**,
**Increase/Decrease priority**. Status, assignment, and lifecycle timestamps are
read-only in the form — they change only through service-layer transitions, not
free edits. Frame ranges are validated (`end > start`).

**Submissions** — QC status and a safely rendered, read-only `qc_report`, plus a
link to the uploaded label file. Actions: **Approve**, **Reject (with
comments)**, **Request revision (with comments)**. Reject/revision use an
intermediate form that **requires a comment**; all three go through
`review_submission()` so task status/timestamps stay consistent.

**Review records** — immutable audit history. Read-only for managers (no add,
no change); only superusers may delete.

**Accounts** — Institutions; **User profiles** (identity, role, institution);
**Annotator profiles** with inline editing of **active status** and
**`max_active_tasks`**, an **at-capacity** filter, activate/deactivate actions,
and links to each annotator's tasks and submissions.

## Safety model

- All workflow actions reuse the existing **service layer**
  (`mark_project_reviewed`, `create_whole_volume_task`, `auto_assign_project`,
  `assign_task_to_annotator`, `review_submission`, …) rather than editing status
  fields directly, and wrap multi-object changes in transactions.
- Bulk actions validate eligibility per object and report successes, warnings,
  and errors via Django messages — they never partially complete silently.
- Managers cannot delete a project that already has downstream tasks (superuser
  override only), cannot edit review history, and cannot edit security-sensitive
  `User` fields.
- Changelists use `select_related`/annotations to avoid N+1 queries and never
  read large TIFF/NIfTI/Zarr data or run QC/model computation while rendering.

## What stays in React / out of scope

- **React SPA**: requester registration & data registration, annotator task
  work and label submission, and the requester/annotator dashboards.
- **Out of scope (unchanged):** payments/wages/billing, in-browser image
  annotation, nnU-Net, Slurm, and other non-MVP features.
