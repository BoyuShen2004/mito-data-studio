# Workflow simplification audit

Audit date: 2026-10-09. Scope: frontend project/data registration, assignment,
volume metadata and pyramids, measurement setup/results, People/teams, task
submission/review, and hard-case navigation. This is a targeted workflow audit,
not a full security, accessibility, or scientific validation audit. API and
permissions are unchanged.

## Findings and disposition

| Priority | Workflow / evidence | User impact | Disposition |
| --- | --- | --- | --- |
| P1 | `frontend/src/pages/VolumeDetailPage.tsx`: a three-second pyramid poll calls `vol.reload`; the loading return unmounts `VolumeMetadataSidebar` | Pending metadata must be typed again; even queueing a build loses edits | Fix: retain the loaded matching volume during refresh, show recoverable refresh errors inline, and reset the sidebar when the volume changes. Regression tests cover polling and errors. |
| P1 | `frontend/src/components/ProjectMeasurements.tsx`: loading/error branches and spacing-dependent component keys unmount measurement controls | Saving voxel size or refreshing volumes resets Saved working draft to Official label and drops setup state | Fix: retain controls for a loaded volume, key setup by volume ID, preserve source across spacing changes, and reload results when spacing changes. Regression tests cover saving, refresh, failure, and changing volumes. |
| P2 | `frontend/src/pages/VolumeDetailPage.tsx`: task-list dependencies previously included the entire refreshed volume object; automatic polling could restart a still-pending refresh | Each status response fetched every project task again, while slow volume responses could be superseded repeatedly | Fix: use project/volume IDs for task queries and pause automatic polling during loading. Regression checks assert one task request through a status refresh and timer cleanup while a request is pending. Full project task-list retrieval remains a scaling limitation. |
| P2 | `frontend/src/pages/HardCaseDetailPage.tsx`: resolving or revoking a case calls reload, whose loading return unmounts the embedded viewer | Viewer controls and navigation can reset after a case action | Deferred: validate viewer camera/coordinate lifecycle and revocation behavior before retaining viewer state. Resolution confirmation remains intentional. |
| P2 | `frontend/src/components/AssignmentPlanEditor.tsx`: “Edit team in People” points to `/people`, without a project parameter or return context | Editing an existing team may require finding the project and reopening assignment | Deferred: define how a return action protects pending assignment changes; do not add navigation that silently loses a draft. Inline team creation/selection already avoids that detour. |
| P3 | `frontend/src/pages/PeoplePage.tsx`: saving a profile reloads the overview and unmounts CollaborationManager | A manager composing a team while editing their own profile may lose the team draft | Deferred: verify authenticated identity refresh and profile form synchronization before changing the page lifecycle. |
| Verified | `frontend/src/pages/SubmitTaskPage.tsx`: completed offline upload displays QC and a link back to the task | One return click, but QC remains visible for inspection | Retain: automatic navigation would hide the submission result. |
| Verified | `frontend/src/components/ReviewBox.tsx`, `frontend/src/pages/TaskDetailPage.tsx`: distinct review decisions and next-waiting link | Explicit decisions with a direct continuation route | Retain: do not automate approval, revision, or rejection. |
| Already fixed | `frontend/src/pages/ProjectDetailPage.tsx`, `frontend/src/pages/RegisterDataPage.tsx` | Team selection previously closed assignment; successful registration required a separate project return | Commit `6def3ee`: retain assignment draft during refresh and open project Data after a complete successful batch; retain failures, uncertain results, unqueued input, and explicit continued registration. |

## Preserved boundaries

Save metadata, Save voxel size, Save plan, Submit, and review decisions remain
explicit. Measurement runs remain manager-only; saving spacing does not queue a
run. Unsaved canvas edits remain excluded from measurements. Changing volumes
starts separate setup state. Login account helpers still only fill the form;
Sign in remains explicit. Deletion, reset and working-team withdrawal safeguards
are retained. No backend services, feature flags, registered scientific source files, or label files
are changed by these UI fixes.

## Validation

Regression tests live in `frontend/src/pages/VolumeDetailWorkflow.test.tsx` and
`frontend/src/components/ProjectMeasurements.test.tsx`; the measurement component
suite also checks scientific source behavior and manager-only run controls.
Initial workflow-pass validation: 90 frontend test files / 690 tests passed;
14 scientific workbench browser tests passed; production build and TypeScript
checking passed; 312 local documentation links checked with zero errors;
`git diff --check` passed. No backend changes were made or backend tests run.
Browser fixtures use synthetic API responses and do not create or modify real
project data. At the time of this local validation, these changes had not been committed or deployed. The follow-up maintainability pass adds bounded automatic status polling and avoids repeated project task-list fetches; its validation is recorded below.

Result freshness is supplied by `backend/annotation/measurement_api.py` through
`input_is_current` in `backend/annotation/measurement_jobs.py`; the frontend now
refetches that status when spacing changes. The regression in
`frontend/src/components/MitoMeasurements.test.tsx` checks the outdated-result
warning and preservation of the working source without automatically running
measurements.

## Maintainability and scalability follow-up

The [architecture reference](../engineering/architecture.md#frontend-refresh-and-draft-ownership)
records state owners, reset boundaries, effect cleanup, and request dependencies.
The user guide remains focused on actions and recovery; engineering details are
kept in engineering documentation. Documentation remains English and maintained
under `docs/`. No new generic state framework, API, or processing backend is
introduced. Capacity and concurrent-user limits have not been benchmarked;
reduced request repetition alone does not prove overall scalability.

Follow-up validation: 90 frontend test files / 691 tests passed; 14 scientific
workbench browser tests passed; production build and TypeScript checks passed;
316 documentation links checked with zero errors; `git diff --check` passed.
The browser poll regression also checks that a status refresh fetches the
project task list only once. A measurement regression confirms changed spacing
clears cached freshness on a failed request and allows explicit result refresh
recovery. README and maintained docs were checked for Chinese text; none was
found. These checks do not substitute for a large-project load test.

## Production promotion correction

The first production alignment to `ed62d3f` matched canonical `main`, but missed
the Track layout fix from `4aec268`, which was running in dev and was absent
from that branch. Comparing only production with canonical did not reveal the
omission. The correction restores Track markup/CSS, its user-guide paragraph,
and both layout browser regressions to canonical before redeploying production.
The production promotion runbook now requires a comparison with running dev as
well. Effective checks found all nine backend feature flags and both chunk
build declarations matched across dev and production; no flags were changed.
