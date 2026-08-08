# 04 — WEBKNOSSOS Task Management Analysis

## Product behavior (**DOC**)

Sources: https://docs.webknossos.org/webknossos/tasks_projects/tasks.html ; `docs/tasks_projects/{concepts,projects,tasks}.md`

1. Create **Task Type** (instructions, tracing type skeleton/volume/hybrid, viewer settings including `volumeInterpolationAllowed`).
2. Create **Project** (team, **priority**, optional time limit, pause/resume).
3. Create **Task**(s): dataset, starting positions / NML, bounding box, **neededExperience**, **totalInstances** (redundant annotations).
4. Annotators open dashboard → **request a task**.
5. System assigns an eligible pending instance → opens annotation.
6. User finishes; managers download/compound-review project annotations.
7. Manual assign / transfer available for admins/team managers.

### Auto-assignment criteria (**DOC**, mirrored in **CODE** `findNextTaskQ`)

- User has required experience domain/value.
- User is in project team (or admin path).
- Task has `pendingInstances > 0`.
- User does not already have an annotation for that task (except cancelled handling for managers).
- Project not paused.
- Order by **project priority DESC**; ties effectively arbitrary (`LIMIT 1` without secondary random in SQL — DOC says random among equal priority; CODE selects first after priority order only — **INFER**: DB order may act as tie-break; Claude Code must re-check if RANDOM() was added later).
- Concurrent open-task limit: non-admins limited by `conf.WebKnossos.Tasks.maxOpenPerUser`; excess open work restricts them to team-manager teams only (**CODE**: `TaskService.getAllowedTeamsForNextTask`).

## Source-level API (**CODE**)

`TaskController.request`:

```
teams ← getAllowedTeamsForNextTask(user)
(taskId, initializingAnnotationId) ← taskDAO.assignNext(user, teams, isTeamManagerOrAdmin)
annotation ← annotationService.createAnnotationFor(...)
return annotation JSON
```

Also: `assignOne`, `peekNext`, bulk create, CSV/NML create-from-files.

## Instance accounting (**CODE**)

Evolution `008-task-instances-triggers.sql`:

- `countsAsTaskInstance(annotation)` ⇔ typ=Task ∧ not deleted ∧ state≠Cancelled.
- INSERT Task annotation → `pendingInstances -= 1`.
- Transition out of counting → `+= 1`.
- CHECK constraints: `0 ≤ pendingInstances ≤ totalInstances`.
- Negative pending prevented; assignment retries on serialization / negative pending (**CODE**: `assignNext` retryCount=50, Serializable isolation).

## Frontend surfaces (**CODE** paths)

- `frontend/javascripts/admin/task/*` — create/list/bulk
- `frontend/javascripts/admin/project/*`
- `frontend/javascripts/admin/tasktype/*`
- `frontend/javascripts/admin/statistic/project_progress_report_view.tsx`
- Dashboard tasks (**DOC**: `docs/dashboard/tasks.md`)

## Gaps vs mito (preview)

| WK | mito today |
|---|---|
| Task Type blueprint | Enum `task_type` on AnnotationTask only |
| Multi-instance tasks | One assignee FK per task |
| Pull “Get next” | Manager push / plan apply |
| Experience domains | `difficulty` / `quality_score` weak proxies |
| Project priority + pause | Project status + task priority only |
| Time tracking per annotation | Limited timestamps |
| Compound project download | Per-submission review flow (different, valuable) |

mito’s **submit → review → reject/revise → lock** loop is actually richer for vendor QC than WK’s finish/download model and must be **preserved** while adopting WK’s assignment maturity.
