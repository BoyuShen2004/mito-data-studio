# 1. Roles, sign-in, and navigation

[User guide](../user-guide.md) · Previous: [Guide index](../user-guide.md) · Next: [Projects and data](02-projects-and-data.md)

## Roles

| Role | Main workspace | Typical responsibilities |
| --- | --- | --- |
| Requester | **My Projects** | Create projects, register server-readable data, describe requested work, monitor progress, and inspect results |
| Manager | **Dashboard** | Approve projects, manage access and teams, assign tasks, review submissions, manage shares, and reopen or reset work |
| Annotator | **My Tasks** | Edit assigned volumes, save drafts, record hard cases, respond to feedback, and submit snapshots |

Pages and API results are permission-aware. Not seeing a button usually means
the current role or project relationship does not permit the action; it is not
necessarily a loading failure.

## Sign-in and development accounts

Choose the portal role on the sign-in page, enter credentials, and press **Sign
in**. When development accounts are enabled, selecting an account only fills
the form and selects the appropriate portal tab. It never authenticates or
navigates automatically.

The red **Clear all existing files** action is a development-only destructive
reset. It is not an account choice and should not be used to fix an individual
task. It clears application data while preserving configured development
identities. Production deployments normally hide this control.

## Global navigation

- The role home is **Dashboard**, **My Projects**, or **My Tasks**.
- **Projects** and **Register Data** are available to managers as appropriate;
  requesters use **My Projects** and **Register Data**.
- **People** shows role-scoped collaborators, project teams, and eligibility.
- **Hard Cases** is the difficult-label inbox for projects the user can access.
- The username opens **Profile**; **Log out** ends the session.
- **Back** returns through application history when possible, then falls back to
  the logical parent page. The product name is display-only.

The application may preserve the selected project tab or viewer location in the
URL. Sharing or bookmarking such an authenticated URL does not grant access.

## Three permissions that are easy to confuse

1. **Project access** lets a person see a project and collaborate on its
   permitted content.
2. **Working-team eligibility** makes an annotator available for assignment on
   that project.
3. **Task assignment** names the one annotator who owns the current
   whole-volume editing task.

Granting access does not assign work. Adding a person to a team does not by
itself expose every project. Managers should verify all three states when an
annotator cannot find a task.

## Common first checks

- If redirected after opening a page, confirm the account role and project
  membership.
- If **Annotate** is absent, confirm the task is open and assigned to the current
  annotator. Managers may inspect more broadly, but inspection does not accrue
  the annotator's time.
- If the page seems stale after a role or access change, return to the role home
  and reopen the project.

[User guide](../user-guide.md) · Next: [Projects and data](02-projects-and-data.md)
