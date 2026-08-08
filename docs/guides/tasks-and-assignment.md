# Tasks and assignment

The current assignment model is deliberately simple: one volume corresponds to
one whole-volume task and one assignee. There are no frame splits, claim queues,
or multiple active assignees for a volume.

Task bounds are stored as zero-based half-open ranges (`z_start` included,
`z_end` excluded). The UI displays them as one-based **Frames (z)**, so a
256-layer whole volume is shown as `1–256`.

## Assign work

1. As a manager, approve the project.
2. In **People**, put eligible annotators in a team.
3. In the project's **Assign** tab, select the working team.
4. Select one assignee per volume from the main table. Each row reads left to
   right as task, current status, volume (format, shape, voxel size, region
   coverage and label type, matching **Data**), and assignee.
5. Open **Details** at the right-hand end of a row to set priority, difficulty,
   deadline, or wrapped multi-line instructions. Under **Annotations**, use the
   vertically stacked **Close annotation / Reopen annotation** and **Reset
   annotations** actions. The control stays labelled **Details** whether the
   row is open or closed; the caret shows which it is.

The **Access** tab is the honest union of explicit project members, everyone in
the project's working team, and any remaining task assignees. It labels those
sources as **Project member**, **Working team**, or **Via assigned task**. A
working-team member appears there even before receiving a task; only explicit
project memberships have a **Remove membership** action.

## Reset an assignee's labels

Open a row's **Details** — the control at the right-hand end of each row in the
**Assign** table — and use **Annotations → Reset annotations**. It discards that
task's working annotation and restores the volume's **registered** label mask,
so an assignee can start from a clean mask without the task being deleted and
recreated.

- Scope is the task's working copy — one volume, one task. The assignment,
  assignee, priority, deadline and instructions are all kept.
- It clears every layer (saved and unsaved), the Track prompt queue, and
  per-label verification state, because all of those describe voxels that no
  longer exist.
- The **registered source mask is never written** — it is only read.
- It asks for confirmation and cannot be undone.
- It is refused on an approved-and-locked task. Reopen annotation first.

The assignee can do the same for their own task from the editor; see
[Annotation basics](annotation-basics.md).

## Task states

| State | Meaning |
| --- | --- |
| Unassigned | Task exists but has no annotator |
| Assigned / In progress | Work is available to the assignee |
| Submitted | Latest saved draft awaits manager review |
| Revision requested / Rejected | Work is returned to the annotator |
| Approved | Official label accepted; annotation may be closed or kept open |

Managers can transfer assignments and can lock or reopen annotation from
**Project → Assign → Details**. An approved-and-closed task loses Annotate
and Submit until a manager reopens it.

`auto_fill` is an operator-run, explicitly enabled scheduler. It never runs just
because the UI is open; deployments that use it should document their cron or
service schedule separately.
