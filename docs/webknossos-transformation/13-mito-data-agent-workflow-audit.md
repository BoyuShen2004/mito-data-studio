# 13 — mito-data-agent Workflow Audit

## A. Project → assign → annotate → review

1. Requester/manager creates Project.
2. Register volumes (`/api/hpc/scan/`, `/api/register-data/`).
3. Manager marks `manager_reviewed`.
4. Each volume becomes one whole-volume AnnotationTask (`ensure_volume_tasks`).
   (Historically a volume could also be split into z-range tasks; that path was
   removed — one volume is one assignable work unit.)
5. Assign: manual / `auto_assign_project` / assignment plan preview+apply.
6. Annotator opens `/editor/tasks/:id` → paint → **explicit Save** → submit-inapp or upload.
7. Manager reviews submission → approve (optional lock) / reject / revision.
8. In-app approve may promote working label → official.

**Maturity:** usable–mature for single-team ops. **Gaps:** no pull queue, no multi-instance consensus, SQLite races, latest-only submission pruning (ReviewRecord preserved).

## B. Dataset → render

Register path → shape detect → `/viewer/volumes/:id` SliceViewer fetches JPEG slices + optional official label PNG.

**Gaps:** no pyramid, no WebGL, process-local caches.

## C. Inference → proofreading

**Intended:** ProcessingJob PREDICT → outputs → proofreading tasks.
**Actual:** register prediction masks from disk; ProcessingJob `on_job_finished` placeholder; create_processing_job unused by domain.

## D. Hard cases

Create HardCase with label_id + optional public token → read-only public viewer API.

## E. Failure modes at scale

- Slider thrash → request pile-up (limited client LRU, no abort priority scheduler).
- Multi-gunicorn workers → memmap LRU coherency / SQLite write contention.
- Track/watershed → full volume RAM.
- No autosave → refresh data loss for unsaved strokes.
