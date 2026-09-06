# 3. People, access, teams, and assignment

[User guide](../user-guide.md) · Previous: [Projects and data](02-projects-and-data.md) · Next: [Viewer](04-viewer.md)

## Set up collaboration in the right order

For a requester-created project, approve it first. Then:

1. Open **People** and add annotators to the project's working team so they are
   eligible for assignment.
2. Open the project **People** tab and grant explicit project access where
   required.
3. Open the project **Tasks** tab and press **Assign volumes** — with rows
   ticked it edits exactly those, with none ticked it opens the whole project's
   plan. Select one assignee for each volume.
4. Set task metadata and select **Save plan**. Unsaved table changes are only a
   local plan.

The assignment editor can filter rows and stage multiple changes. Review the
dirty-row count on **Save plan (N)** before committing a large plan.

## Whole-volume task model

One registered volume maps to one whole-volume task with at most one active
assignee. The interface does not split a volume into frame ranges for multiple
simultaneous annotators. The displayed layer range describes the volume task,
not independent ownership slices.

Transferring a task changes who owns future work. Existing audit history and
already-recorded time remain attributed to the person who performed them.

## Task metadata

Expand a row's details to set:

- priority for scheduling;
- difficulty for planning and reporting;
- deadline;
- instructions or notes for the annotator;
- whether annotation is open or closed.

An approved-and-closed task is viewable but cannot be painted or submitted
until a manager reopens it. Access alone does not override that lock.

## Reset annotations

**Reset annotations** is a destructive whole-task operation. It discards the
working annotation, pending or saved edits represented by that working copy,
Track prompts/progress, and per-label verification state, then restores the
registered starting label mask. It keeps the task and assignment and never
modifies the registered source file.

Reset cannot be undone. If a task is approved and locked, reopen it before
resetting. Use ordinary Undo for a recent browser edit and request a revision
for review workflow corrections; neither requires destroying the whole draft.

## Diagnose missing work

When an annotator cannot see a volume, verify:

- the project is approved;
- the annotator is eligible through the working team;
- project access is present;
- the task is actually assigned to that account;
- the task has not been closed or reassigned;
- the annotator is looking under the correct Home tab (**Assigned to me** for
  new work, **Needs revision** for anything handed back).

[User guide](../user-guide.md) · Previous: [Projects and data](02-projects-and-data.md) · Next: [Viewer](04-viewer.md)
