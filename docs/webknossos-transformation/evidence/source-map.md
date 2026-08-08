# Evidence Source Map

For each important conclusion: URL / repo / path / symbol / support type.

| ID | Conclusion | Support | Repository | Path / URL | Symbol / section |
|---|---|---|---|---|---|
| E01 | Local clone is webknossos-libs, not main app | CODE | local | `/home/weidf/shenb/webknossos-libs` | `git remote -v` → scalableminds/webknossos-libs |
| E02 | Main WK license AGPL-3.0 | DOC/CODE | scalableminds/webknossos | `LICENSE`, GitHub license API | AGPL-3.0 |
| E03 | Python `webknossos` package AGPL-3.0 | CODE | webknossos-libs | `webknossos/LICENSE`, `pyproject.toml` | license AGPL-3.0 |
| E04 | cluster_tools MIT | CODE | webknossos-libs | `cluster_tools/LICENSE` | MIT |
| E05 | WK stack: Scala Play + React/TS/Redux/Saga/Three/antd | CODE | webknossos | `CLAUDE.md`, `package.json`, `build.sbt` | — |
| E06 | Services: app, datastore, tracingstore, fossildb | CODE | webknossos | top-level dirs | — |
| E07 | Task concepts: Task/Instance/Type/Project/Experience | DOC | webknossos | `docs/tasks_projects/concepts.md` | — |
| E08 | Auto-assign criteria (experience, team, priority, pause, multi-instance) | DOC | docs.webknossos.org | `/webknossos/tasks_projects/tasks.html` | Automatic Task Assignment |
| E09 | `POST` request assigns via `taskDAO.assignNext` | CODE | webknossos | `app/controllers/TaskController.scala` | `def request` |
| E10 | Eligibility SQL joins experiences, filters pending, orders priority | CODE | webknossos | `app/models/task/Task.scala` | `findNextTaskQ` |
| E11 | Claim uses Serializable isolation + 50 retries | CODE | webknossos | `app/models/task/Task.scala` | `assignNext` |
| E12 | pendingInstances maintained by PG triggers | CODE | webknossos | `schema/evolutions/008-task-instances-triggers.sql` | `onInsertAnnotation` |
| E13 | max open tasks gates team set | CODE | webknossos | `app/models/task/TaskService.scala` | `getAllowedTeamsForNextTask` |
| E14 | Interpolation = SDF + linear blend; active ID; max depth 100 | CODE | webknossos | `frontend/.../volume_interpolation_saga.ts` | `signedDist`, `maybeInterpolateSegmentationLayer` |
| E15 | Interpolation gated by `volumeInterpolationAllowed` | CODE/DOC | webknossos | saga + evolution 082 | — |
| E16 | PullQueue priority/abort/batch | CODE | webknossos | `frontend/.../pullqueue.ts` | `PullQueue` |
| E17 | Datastore serves multi-bucket binary data | CODE | webknossos | `BinaryDataController.scala` | `requestViaWebknossos` |
| E18 | Formats Zarr/WKW/N5/NG | DOC/CODE | webknossos + libs | README; `DataFormat` enum | — |
| E19 | Hosted AI analysis not default OSS | DOC | docs | `automation/ai_segmentation.md` | info box webknossos.org only |
| E20 | MitoNet mitochondria model on hosted WK | DOC | docs | ai_segmentation | Mitochondria Detection |
| E21 | mito uses Django+SQLite+Canvas2D+TIFF slices | CODE | mito-data-agent | `settings.py`, `AnnotationCanvas.tsx`, `slice_io.py` | — |
| E22 | mito task = single assignee AnnotationTask | CODE | mito-data-agent | `annotation/models.py` | `AnnotationTask.assigned_to` |
| E23 | mito review/lock loop mature | CODE | mito-data-agent | `annotation/services.py` | `approve_submission`, `annotation_locked` |
| E24 | ProcessingJob unused by domain; on_job_finished placeholder | CODE | mito-data-agent | `processing/services.py` | — |
| E25 | EfficientSAM/SAM2 vendored and live | CODE | mito-data-agent | `vendor/`, cellable_port AI | — |
| E26 | No Docker/Cloudflare in mito tree | CODE | mito-data-agent | repo search | — |
| E27 | WK release inspected 26.08.0 | DOC | GitHub releases | api.github.com/.../releases/latest | 2026-07-23 |
| E28 | webknossos-libs HEAD 0419d102 / tag v3.5.6 | CODE | local git | — | — |
| E29 | Sharing via links + permissions | DOC | webknossos | `docs/sharing/index.md` | — |
| E30 | Teams organize permissions & tasks | DOC | docs | `/webknossos/users/teams.html` | — |

Claude Code must re-verify E10–E17 against a local clone of `scalableminds/webknossos` before implementing.
