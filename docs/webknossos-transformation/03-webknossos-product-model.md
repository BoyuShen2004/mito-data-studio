# 03 — WEBKNOSSOS Product Model

**Evidence tags:** DOC = official docs; CODE = main-repo source

## Top-level concepts (verified)

```
Organization
├── Users (Admin / Team Manager / User; experience domains)
├── Teams (default team + additional; dataset & task scoping)
├── Folders + Datasets (layers, mags, datastore binding)
├── Projects (priority, pause/resume, team, time limit)
│   └── Tasks
│       ├── Task Type (blueprint: instructions, tracing type, settings)
│       ├── neededExperience (domain, value)
│       ├── totalInstances / pendingInstances
│       ├── editPosition, editRotation, boundingBox
│       └── Task Instances ≡ Annotations of typ=Task
│           ├── state: Initializing → Active → Finished | Cancelled
│           ├── tracingTime
│           └── Volume and/or Skeleton layers (TracingStore)
├── Standalone Annotations (Explorational)
├── Sharing / short links / visibility
├── Jobs / AI analysis (hosted / commercial features differ)
└── Statistics / time tracking / compound downloads
```

### Notes on the sketch above

- **Task Instance** is a product concept (**DOC**: `docs/tasks_projects/concepts.md`). In the database it is realized primarily as an **Annotation** with `typ = Task` linked to `_task`, not a separate `task_instances` table after evolution 008 (**CODE**: `schema/evolutions/008-task-instances-triggers.sql`).
- `pendingInstances` (historically `openInstances`) is a denormalized counter maintained by PostgreSQL triggers on annotation insert/update/delete (**CODE**).
- Organizations own users, teams, datasets, experience domains (**DOC**: teams/orgs docs; **CODE**: `app/models/organization/`, `Experience.scala`).

## Product model relationships

| Concept | Relates to | Purpose |
|---|---|---|
| Organization | Teams, Users, Datasets | Multi-tenant boundary |
| Team | Users, Projects, Dataset permissions | Access + task eligibility |
| Experience | User ↔ Task.neededExperience | Skill gating for auto-assign |
| Task Type | Team, settings JSON | Reusable annotation blueprint |
| Project | Team, priority, paused | Work queue grouping |
| Task | Project, TaskType, Dataset, instances | Unit of assignable work |
| Annotation (Task) | User, Task | Concrete instance + editable tracing |
| Datastore | Dataset bytes | Scalable volume delivery |
| TracingStore / FossilDB | Annotation layers | Mutable annotation persistence |

## Roles (product)

| Role | Capabilities (DOC/CODE) |
|---|---|
| Admin | Org-wide teams, datasets, tasks; all teams |
| Team Manager | Manage team members; create/assign tasks for team projects |
| User / Annotator | Request tasks, annotate, finish |
| External collaborator | Via sharing links / invitations (visibility + permissions) |

## What mito must map

mito today: `Institution` ≈ weak org; roles `manager|annotator|requester`; no Teams; no Task Types; no Task Instances; Project ≠ WK Project semantics.

Target mapping is defined in `16-target-domain-model.md`.
