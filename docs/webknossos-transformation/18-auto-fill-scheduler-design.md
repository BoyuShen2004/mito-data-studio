# 18 — Auto-Fill Scheduler Design

**Beyond WEBKNOSSOS.** Terminology: **available user** (never “empty people”).

## Modes

| Mode | Behavior |
|---|---|
| Pull | User claims next |
| Push (auto-fill) | Scheduler assigns to available users with capacity |
| Hybrid | Scheduler proposes; manager approves (`dry_run` → apply) |

## Available user criteria (configurable)

- enabled account; team membership; eligible role
- recently active within N days
- active assignments < max_concurrent
- no overdue blocking assignment
- within work-hours window (optional)
- meets experience/skills
- not on leave / not manually paused / not project-excluded

## Scoring (deterministic)

```
score = w1*project_priority + w2*task_priority + w3*deadline_urgency
      + w4*skill_match + w5*quality_history - w6*current_load
      + w7*locality + w8*fairness_bonus - w9*rejection_rate
```

Weights in config; each decision writes `SchedulerDecision` row: candidates considered, scores, winner, mode, actor.

## Safety

- Same claim transaction as pull (reuse `assign_instance` primitive)
- Idempotency keys per scheduler tick
- Metrics: assignments/min, conflict retries, starve rate
- Tests: concurrent workers, fairness, no double assign
- Manual override always wins
