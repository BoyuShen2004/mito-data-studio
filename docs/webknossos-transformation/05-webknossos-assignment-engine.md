# 05 — WEBKNOSSOS Assignment Engine

## End-to-end claim path (**CODE**)

```
User clicks Request Task
  → TaskController.request
  → TaskService.getAllowedTeamsForNextTask
       • Admin: all org teams
       • Else if open non-admin tasks < maxOpenPerUser: user's teams
       • Else: only team-manager teams (or fail tooManyOpenOnes)
  → TaskDAO.assignNext(userId, teamIds, isTeamManagerOrAdmin)
       • INSERT annotation ... SELECT FROM findNextTaskQ(...)
       • TransactionIsolation = Serializable
       • retry up to 50× on serialization errors or "Negative pendingInstances"
  → DB trigger onInsertAnnotation decrements pendingInstances
  → AnnotationService.createAnnotationFor materializes tracing layers
  → Client opens annotation
```

## Eligibility SQL (`findNextTaskQ`) — paraphrased (**CODE**: `app/models/task/Task.scala`)

```sql
SELECT tasks.*
FROM tasks
JOIN user_experiences ON domain/value match (task.needed <= user.value)
JOIN projects ON task.project = project.id
LEFT JOIN user_task_annotations ON same task & user
WHERE pendingInstances > 0
  AND project.team IN (:teamIds)
  AND user has no prior task annotation (with manager cancelled exception)
  AND NOT project.paused
ORDER BY project.priority DESC
LIMIT 1
```

## Race-prevention strategy (**CODE**)

| Mechanism | Role |
|---|---|
| Serializable transaction on INSERT…SELECT | Prevents two users claiming last instance |
| `pendingInstances` CHECK ≥ 0 | Hard fail if over-claimed |
| Trigger decrement on insert | Single source of truth for remaining capacity |
| Retry loop (50) | Absorbs serialization conflicts under load |
| Annotation state `Initializing` | Allows abort/cleanup if tracing creation fails (`abortInitializedAnnotationOnFailure`) |
| Immutable `_task`/`typ` on annotations | Prevents rewiring instances |

## Manual path

`assignOneTo(taskId, userId)` uses `findNextTaskByIdQ` requiring `pendingInstances > 0`, same Serializable insert pattern.

## What mito must copy conceptually

1. Explicit **instance capacity** counter with DB enforcement.
2. **Atomic claim** (not read-then-update in Python without locks).
3. **Eligibility query** separated from materialization.
4. **Retry + idempotent cleanup** on partial failure.
5. **Pull API** + optional manual assign.
6. PostgreSQL (or equivalent) — SQLite cannot provide the same Serializable story for multi-worker production.

## Auto-fill (beyond WK)

WK is **pull-centric**. mito target adds **push / hybrid auto-fill** — see `18-auto-fill-scheduler-design.md`. Do not claim WK already has push auto-fill; it does not as a first-class product feature (**DOC** describes request + manual assign only).
