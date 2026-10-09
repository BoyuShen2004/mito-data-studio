# Start here as a new team member

Mito Data Studio brings microscopy images, mitochondria annotation, assignment,
review and measurement into one web workbench. Images record intensity;
integer instance IDs identify voxels belonging to the same object. The
application supports collaboration; biological correctness still requires
independent validation of annotations and measurements.

## First day: learn the workflow

1. Ask your team lead for the development-instance URL, your account and a
   practice task. Use practice data rather than a live production task. You do
   not need to deploy your own service to use an existing instance.
2. Read the [glossary](glossary.md) to distinguish image, label, volume, task and ROI.
3. Follow the instructions for your role below. Editing and review controls depend
   on your account, assignment and task state; the backend also checks permissions.

| Role | First action | Read |
| --- | --- | --- |
| Annotator | Home → Assigned to me → task; open View, then Annotate; select a label, edit a few voxels and Save | [Viewer](../user-guide/04-viewer.md), [annotation tools](../user-guide/05-annotation-tools.md) |
| Manager | Open Review for a submitted task, inspect the submission and choose a review decision | [Assignment](../user-guide/03-people-and-assignment.md), [review](../user-guide/07-submit-and-review.md) |
| Requester | Create a project, prepare data paths and learn registration and project approval | [Projects and data](../user-guide/02-projects-and-data.md) |

Submit and Approve change task state even in practice. Agree on the exercise
with the person responsible for the practice task. If a task is missing, check
your role, project access and assignment separately.

## Remember the data flow

```text
Registered source image (read-only) + initial label (optional)
                                      ↓
Browser pending edits → successful save → working label
                                      ↓ Submit
                              immutable submission snapshot
                                      ↓ Approve
                                  official label
```

- **Save** persists a draft; **Submit** creates a snapshot for review.
- **Approve** installs the chosen snapshot as the official label. Working-team
  withdrawal can also promote a saved draft without approval, so official does
  not always mean reviewed.
- The editor attempts autosave every 30 seconds and when the tab is hidden.
  Still press **Save** and check success before leaving or submitting. An
  unresolved Track preview may also be autosaved; see the
  [known limitation](../user-guide/06-assisted-and-track.md#failure-and-safety-rules).
- A SAM2 proposal requires inspection. Accepting a proposal and saving labels
  are separate actions.
- Measurements read the official label or saved working draft and do not modify
  annotation state. Browser-only pending edits are excluded.
- Public shares are read-only. Physical length and volume require real voxel spacing.

## Continue with your goal

- Use the application: [complete workflow](../user-guide/workflows.md), [Measurements](../user-guide/09-measurements.md).
- Understand the project: [product overview](../overview.md), [code map](../engineering/code-map.md).
- Develop: [first contribution](first-contribution.md), then the [feature walkthrough](../engineering/feature-walkthrough.md).
- Write a manuscript or report: [research materials](../research/README.md). Implemented features, passing tests and scientific validity require different evidence.

## Check your understanding

Explain the difference between Save and Submit, ROI and instance labels, and
why different IDs denote different objects. Unknown voxel spacing must not be
replaced with 1 merely to make measurements run. When reporting a problem,
include the task ID, reproduction steps and error message rather than credentials.
