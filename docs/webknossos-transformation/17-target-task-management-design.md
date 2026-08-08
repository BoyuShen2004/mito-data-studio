# 17 — Target Task Management Design

## Principles

1. Adopt WK hierarchy: TaskType → Project → Task → Instances.
2. Keep mito review/QC semantics (approve/reject/revise/lock) — superior for vendor workflows.
3. Pull assignment as default annotator UX (“Get Next Task”).
4. Manual assign + transfer for managers.
5. Multi-instance tasks for consensus when configured.
6. Postgres Serializable or equivalent claim transaction + instance counter triggers/constraints.
7. Feature-flag parallel run with legacy AnnotationTask during migration.

## Pull mode (WK-parity)

Eligibility: team, role, experience, capacity (`max_active_tasks`), project not paused, pendingInstances>0, duplicate-instance rules, skill tags.

Selection: order by project.priority, task.priority, due date; deterministic tie-break (task id) plus optional weighted random.

API: `POST /api/tasks/claim-next/` → returns assignment + opens editor.

## Push / hybrid

See `18-auto-fill-scheduler-design.md`.

## Dashboards

Port concepts from WK project progress + mito lifecycle buckets + People pages.

## Migration

Map existing AnnotationTask → Task with `totalInstances=1`, create TaskType from enum, backfill Assignment from `assigned_to`.
