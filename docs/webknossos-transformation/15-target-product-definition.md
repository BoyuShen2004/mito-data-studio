# 15 — Target Product Definition

## One-sentence goal

Build a **production-grade mitochondria annotation, inference, QC, and crowdsourcing platform** whose **engineering maturity** matches WEBKNOSSOS, while preserving mito-data-studio’s domain workflows and familiar proofreading UI.

## End-to-end target workflow

```
External/internal project request
 → dataset + raw/seg registration (HPC index)
 → model inference (nnU-Net / Slurm) + prediction versioning
 → task generation (tiles / uncertainty / whole volume)
 → scheduling (pull + auto-fill push + hybrid)
 → annotator 3D/2D proofreading (familiar UI)
 → hard-case escalation + deep links
 → review / reject / resubmit
 → approval + lock
 → QC report + compensation/vendor tracking (if enabled)
 → retraining dataset packaging
```

## Quality bar (WEBKNOSSOS-level)

Not a visual clone. Required properties:

- Concurrency-safe task assignment
- Complete task hierarchy & permissions
- Durable annotation ops with undo/autosave/recovery
- Chunked multiresolution volume IO
- Responsive slider navigation under load
- Bounded memory in multi-hour sessions
- Observability, backups, rollback, load/soak tests
- License/attribution completeness

## Must preserve (mito-specific)

- Mitochondria workflows & QC taxonomy (false merge/split, missing, boundary, leakage)
- HPC directory indexing & nnU-Net pairing heuristics
- EfficientSAM / SAM2 interactive tools
- Hard-case registry
- Submit/review/revise/lock semantics
- Familiar proofreading chrome (`AnnotateToolChrome`, tool patterns)
- Institution/requester project intake
- Slurm path (complete it)
- Prediction version / model lineage (implement properly)

## Explicitly allowed

Large refactors, new services, DB redesign (Postgres), frontend architecture upgrades, AGPL-compliant reuse.
