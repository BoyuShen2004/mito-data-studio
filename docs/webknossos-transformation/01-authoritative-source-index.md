# 01 — Authoritative Source Index

Prefer these sources over blogs/marketing summaries.

## Official repositories

| Source | URL | Role |
|---|---|---|
| Main app | https://github.com/scalableminds/webknossos | Play/Scala backend, React/TS frontend, datastore, tracingstore |
| Python libs | https://github.com/scalableminds/webknossos-libs | Dataset formats, REST client, CLI |
| Product site | https://webknossos.org/ | Hosted product |
| Docs | https://docs.webknossos.org/ | User + ops documentation |
| Nature Methods | https://doi.org/10.1038/NMETH.4331 | Foundational publication (2017) |

## Documentation pages used (primary)

| Topic | URL |
|---|---|
| Overview | https://docs.webknossos.org/webknossos/ |
| Tasks | https://docs.webknossos.org/webknossos/tasks_projects/tasks.html |
| Task concepts | `docs/tasks_projects/concepts.md` in main repo |
| Projects | https://docs.webknossos.org/webknossos/tasks_projects/projects.html |
| Teams | https://docs.webknossos.org/webknossos/users/teams.html |
| Volume tools | https://docs.webknossos.org/webknossos/volume_annotation/tools.html |
| Proofreading | `docs/proofreading/` in main repo |
| Sharing | `docs/sharing/index.md` |
| AI segmentation | https://docs.webknossos.org/webknossos/automation/ai_segmentation.html |
| External datastore | https://docs.webknossos.org/webknossos/datasets/external_storage.html |
| Open-source install | https://docs.webknossos.org/webknossos/open_source/installation.html |

## Critical source modules (main app, master)

| Domain | Path |
|---|---|
| Task assignment API | `app/controllers/TaskController.scala` (`request`, `assignOne`, `peekNext`) |
| Task DAO / locking | `app/models/task/Task.scala` (`assignNext`, `findNextTaskQ`) |
| Task service | `app/models/task/TaskService.scala` |
| Task instance triggers | `schema/evolutions/008-task-instances-triggers.sql` |
| Project model | `app/models/project/Project.scala` |
| Annotation service | `app/models/annotation/AnnotationService.scala` |
| Volume interpolation | `frontend/javascripts/viewer/model/sagas/volume/volume_interpolation_saga.ts` |
| Chunk pull queue | `frontend/javascripts/viewer/model/bucket_data_handling/pullqueue.ts` |
| Data cube / buckets | `frontend/javascripts/viewer/model/bucket_data_handling/data_cube.ts` |
| Prefetch | `frontend/javascripts/viewer/model/bucket_data_handling/prefetch_strategy_plane.ts` |
| Datastore binary API | `webknossos-datastore/.../BinaryDataController.scala`, `BinaryDataService.scala` |
| Chunk cache | `webknossos-datastore/.../ChunkCacheService.scala` |
| Dev stack notes | `CLAUDE.md` (TS/React/antd/Redux/Saga/Three.js; Scala Play) |

## Local mito-data-agent evidence

| Domain | Path |
|---|---|
| Project overview | `progress/PROJECT.md` |
| Models | `backend/{accounts,projects,volumes,annotation,processing}/models.py` |
| Task services | `backend/annotation/services.py` |
| Slice IO | `backend/annotation/visualization/slice_io.py` |
| Viewer | `frontend/src/features/viewer/AnnotationCanvas.tsx` |
| Routes | `frontend/src/routes/AppRoutes.tsx` |

## Confidence legend (used in later docs)

| Tag | Meaning |
|---|---|
| **CODE** | Verified in source |
| **DOC** | Stated in official documentation |
| **INFER** | Reasonable inference from multiple sources; Claude Code must re-verify |
