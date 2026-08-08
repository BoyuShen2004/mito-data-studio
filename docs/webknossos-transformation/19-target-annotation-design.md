# 19 — Target Annotation Design

## UI preservation mandate

- Keep overall proofreading UI and interaction patterns (`AnnotationCanvas`, `AnnotateToolChrome`, tool semantics users know).
- Add missing tools in the **same visual language**.
- Prefer feature flags for new tools.
- Improve internals (ops, autosave, chunk IO) under the hood.

## Priority tool backlog

| Priority | Feature | Source inspiration | Notes |
|---|---|---|---|
| P0 | Interpolation (SDF) | WK `volume_interpolation_saga.ts` | Preview+confirm atomic undo |
| P0 | Autosave + recovery | WK PushQueue concepts | Don’t lose strokes on refresh |
| P0 | Op-log undo/redo | WK stroke model | Replace full-slice snapshots |
| P1 | Flood fill 2D (+ limited 3D) | WK fill tool | Classical, not only SAM |
| P1 | Overwrite policies | WK overwrite modes | empty-only vs everything |
| P1 | Deep links (xyz, label, hard-case) | WK sharing + mito HardCase | |
| P2 | Contour/trace tool | WK trace | If fits UI |
| P2 | Brush presets / shortcuts | WK | |
| P2 | Segment metadata panel | WK segments tab | |
| P2 | Label locking | WK locks | |
| P3 | Agglomerate proofreading | WK tracingstore | Optional; mito split/merge may suffice |
| — | EfficientSAM/SAM2 | mito | **Retain** |
| — | Split/Merge/Watershed | mito | **Retain**; optimize IO |

## Interpolation acceptance (mito)

Workflow: label A → label B → Interpolate → **preview** → confirm/cancel → one undoable op.

Must support: axis X/Y/Z, anisotropy, active label, SDF method, holes/components, collisions/overwrite, max gap, progress/cancel, tests vs golden masks.
