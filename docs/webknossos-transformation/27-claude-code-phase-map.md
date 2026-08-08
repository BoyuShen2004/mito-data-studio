# 27 — Claude Code Phase Map

| Phase | Name | Depends on | Gate |
|---|---|---|---|
| 0 | Baseline, backups, tests, benchmarks | — | numbers stored |
| 1 | Domain model & permissions | 0 | schema approved |
| 2 | WK-style task hierarchy | 1 | migrations dry-run |
| 3 | Concurrency-safe assignment | 2 | race tests pass |
| 4 | Auto-fill scheduler | 3 | dry-run demos |
| 5 | Review/reject/resubmit hardening | 2 | parity with current UX |
| 6 | Dashboards & statistics | 3–5 | manager acceptance |
| 7 | Annotation operation model | 0–1 | op log design approved |
| 8 | Interpolation | 7 | golden tests |
| 9 | Additional annotation tools | 7–8 | ranked P1 done |
| 10 | Autosave/undo/recovery | 7 | soak refresh tests |
| 11 | Volume storage & pyramids | 0 | format decision locked |
| 12 | Chunk/datastore service | 11 | authz + metrics |
| 13 | Frontend chunk cache/scheduler | 12 | p95 scrub target |
| 14 | Rendering/nav redesign | 13 | UI familiarity check |
| 15 | Mesh & large-label scale | 13–14 | memory budgets |
| 16 | Hard-case sharing & deep links | 5,14 | security review |
| 17 | AI inference & SAM endpoints | 11–12 | job lineage |
| 18 | HPC/Slurm integration | 17 | end-to-end predict |
| 19 | Observability | any | dashboards live |
| 20 | Load & soak tests | 12–15 | pass criteria |
| 21 | Production migration | all prior gates | go-live checklist |
| 22 | License & attribution verification | continuous | compliance sign-off |

Execute each phase as **vertical slices** (model→API→UI→tests→docs), never as one mega-PR.
