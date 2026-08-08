# 16 — Target Domain Model

Names may map onto existing tables via migrations; behavior is mandatory.

```
Organization (evolve Institution)
├── Team
│   ├── TeamMembership (role: member|manager)
│   └── DatasetACL / ProjectACL
├── User + Experience(domain, value)
├── Dataset
│   ├── VolumeLayer (raw | prediction | official | working overlay)
│   └── StorageDescriptor (zarr/tiff URI, datastore id)
├── Project
│   ├── priority, paused, team, template
│   ├── TaskType (reusable; instructions; tool policy; interpolationAllowed)
│   └── Task
│       ├── neededExperience, priority, bbox, start pose, estimate
│       ├── totalInstances / pendingInstances
│       └── Assignment / TaskInstance
│           ├── AnnotationSession (op log, timing)
│           ├── Submission (keep mito review richness; prefer append-only history)
│           ├── Review / Revision (immutable)
│           └── HardCase links
├── SchedulerDecision (audit for auto-fill)
└── ProcessingJob (inference lineage)
```

## State machines (required)

**Assignment:** `pending → claimed → in_progress → submitted → under_review → approved|rejected|revision_requested → (resubmit loop) → closed|cancelled`

**AnnotationLock:** retained from mito as orthogonal gate.

**Project:** `draft → active → paused → archived`

Use Postgres CHECK/ENUM + explicit transition table; forbid illegal jumps in service layer **and** DB constraints where feasible.
