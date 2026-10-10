# 3. People, access, teams, and assignment

[User guide](../user-guide.md) · Previous: [Projects and data](02-projects-and-data.md) · Next: [Viewer](04-viewer.md)

People uses [expandable sections and records](progressive-disclosure.md).
Managers open **Teams & assignment eligibility** for existing teams; expand an
individual team only when there are multiple teams. New team is a regular form.
Multi-person rosters and their records expand on demand; singleton rosters show
details directly. This does not change permissions.

## Assistant-manager access

Primary managers can grant/revoke assistant-manager access at the bottom of an
annotator's expanded record, with confirmation. The **Assistant managers** section
is collapsible even with one person and omits Time reports. Each person's role
appears once in the record heading, including both roles when applicable.
Assistant managers appear in both rosters;
their ordinary annotator eligibility remains unchanged. Assistant managers can
manage projects in the Manager workspace but cannot delegate this account-level
access. See [workspace switching](01-roles-and-navigation.md#assistant-managers-and-workspace-switching).

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

Choose or create the working team directly inside **Assign volumes**. Saving the
team keeps this editor open, including pending task metadata and the rows you
selected. Team eligibility updates immediately; task assignments still require
**Save plan**. Switching an existing working team retains its withdrawal
confirmation and clears pending assignees who are no longer eligible.

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

## Working-team changes and withdrawal

Managers manage the working team from **People** or the project's **People**
tab. With team features enabled, grants mirror team members into project access;
removing a mirrored grant preserves separately granted explicit access. Team
eligibility and task assignment remain separate: access never assigns a task.

Replacing/removing a working team, removing an affected member, or deleting the
team can withdraw assignments outside the retained team. Review the confirmation
before applying a withdrawal. The server promotes each affected volume's saved
working TIFF to its official label reference when that file exists, even without
submission approval. Unsaved browser edits are excluded. It voids pending
submissions, clears assignment/current task decision fields, and returns the task
to the unassigned pool. A durable withdrawal record appears in the former
annotator's Done history; recorded time remains. If no working file exists, the
previous official label remains. This is consequential data-state behavior,
not merely a change to who can see the project.

## Reset annotations

**Reset annotations** is a destructive whole-task operation. It discards the
working annotation, pending or saved edits represented by that working copy,
Track prompts/progress, and per-label verification state, then restores the
registered reset seed (the latest approved label after an approval, otherwise
the original starting mask). It keeps the task and assignment and never
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
