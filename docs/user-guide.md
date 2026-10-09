# Mito Data Studio user guide

This guide describes the current main-branch behavior of Mito Data Studio.
Controls are permission-aware: a requester, manager, annotator, and public-link
visitor can open the same data but see different actions.

New to the lab? Start with the [Chinese onboarding guide](getting-started/README.md) and [glossary](getting-started/glossary.md).

## Choose a module

| Module | Use it to learn |
| --- | --- |
| [1. Roles, sign-in, and navigation](user-guide/01-roles-and-navigation.md) | What each role can do, how access differs from assignment, and where pages live |
| [2. Projects and data registration](user-guide/02-projects-and-data.md) | Create and approve projects; scan and register image, ROI, and label files |
| [3. People, access, teams, and assignment](user-guide/03-people-and-assignment.md) | Make data visible, make annotators eligible, and assign one whole-volume task |
| [4. Viewer and volume inspection](user-guide/04-viewer.md) | Navigate 3-D data, adjust display layers, inspect labels, and use read-only mode |
| [5. Manual annotation tools](user-guide/05-annotation-tools.md) | Select labels, paint, erase, merge, split, fill, watershed, interpolate, undo, and save |
| [6. Region-only, assisted masks, and SAM2 Track](user-guide/06-assisted-and-track.md) | Protect content outside an ROI and review AI-assisted proposals safely |
| [7. Save, submit, review, and feedback](user-guide/07-submit-and-review.md) | Understand drafts and snapshots, both submission channels, decisions, and label comments |
| [8. Hard cases, sharing, time, profile, and safety](user-guide/08-collaboration-and-safety.md) | Collaborate, publish revocable read-only links, interpret time, and recover safely |
| [9. Measurements](user-guide/09-measurements.md) | Select a volume and label source, confirm physical spacing, run measurements and export results |

## Recommended end-to-end path

1. A requester or manager [creates a project and registers data](user-guide/02-projects-and-data.md).
2. A manager approves the project, then configures [access, team eligibility,
   and assignment](user-guide/03-people-and-assignment.md).
3. The assigned annotator inspects the task in the [viewer](user-guide/04-viewer.md),
   edits it with [manual](user-guide/05-annotation-tools.md) or
   [assisted](user-guide/06-assisted-and-track.md) tools, and explicitly saves.
4. The annotator submits a snapshot; the manager [reviews it and returns
   instance-specific feedback or approves it](user-guide/07-submit-and-review.md).
5. Project members use [hard cases and controlled sharing](user-guide/08-collaboration-and-safety.md)
   when discussion or external read-only inspection is needed.

## Core mental model

- Registered images and region masks are immutable source data.
- A working label is separate from registered sources. Browser changes are pending
  until a successful save. The editor also attempts autosave every 30 seconds and
  when the tab is hidden; explicitly **Save** and check the result before Submit.
- **Save** updates the working draft; **Submit for review** creates an immutable
  review snapshot. They are different operations.
- Project access, team eligibility, and task assignment are three separate
  permissions. A person may have one without the others.
- Inspect SAM2 proposals and whole-volume plans before accepting them. Accepted
  edits enter the ordinary draft buffer. Track previews already enter that buffer
  before Confirm; see the [persistence caveat](user-guide/06-assisted-and-track.md#failure-and-safety-rules).
- Public links are read-only. Database-backed project/dataset/volume and hard-case
  links are revocable; legacy signed task links have no per-link revocation or
  age expiry. See [sharing](user-guide/08-collaboration-and-safety.md#public-sharing).

For operators and developers, return to the [documentation index](index.md).
