# RC1 feature-flag decision table

All flags default off. The first staging boot exactly mirrors the live 18188
configuration. A second staging-only build enables the already integrated
annotation/read-path stack so it can be tested against the restored copy; that
does not imply a production recommendation.

| Feature / flag | Dependencies | Backend/API | Frontend | Real-data readiness | Browser / soak evidence before staging | Initial staging | Validation staging | RC1 production recommendation |
|---|---|---|---|---|---|---|---|---|
| Teams — `FEATURE_TEAMS` | none | implemented/tested | no complete team admin | legacy memberships not backfilled | backend only | off | off | off |
| Task hierarchy — `FEATURE_TASK_HIERARCHY` | teams for policy use | implemented/tested | no complete hierarchy UI | legacy instances exist but product backfill needs review | backend smoke | off | off | off |
| Pull claim — `FEATURE_TASK_CLAIM` | task hierarchy | endpoint tested | no claim UI | not exercised on restored rows | backend concurrency only | off | off | off |
| Auto-fill scheduler — `FEATURE_AUTO_FILL_SCHEDULER` | task hierarchy | service/CLI tested | manager uses legacy assignment plan | not exercised on restored rows | backend concurrency only | off | off | off |
| Review history — `FEATURE_REVIEW_HISTORY` | none | implemented/tested | current submission review UI is independent | restored review rows migrate | backend smoke | off | off | off for parity |
| Dashboards — `FEATURE_DASHBOARDS` | none | read-only API tested | no release dashboard client | safe but no user surface | backend smoke | off | off | off |
| Annotation operations — `FEATURE_ANNOTATION_OPS` | none | implemented/tested | undo/redo/editor integration | real staging Save and two-user conflict checks passed | 1226 backend + mounted/browser | off | on | off for RC1; enable only after the two-hour gate |
| Interpolation — `FEATURE_INTERPOLATION` | operations to apply | implemented/tested | editor tool integrated | staging-only apply required | golden/unit/mounted tests | off | on | keep off until staging correctness passes |
| Annotation tools — `FEATURE_ANNOTATION_TOOLS` | operations to apply | implemented/tested | Box/Point/fill/deep-link UI integrated | staging-only apply required | unit/mounted tests | off | on | keep off until staging correctness passes |
| Autosave/recovery — `FEATURE_AUTOSAVE_RECOVERY` | operations for deltas | implemented/tested | authenticated, versioned Save/recovery integrated | real Save/reload passed; same-task stale write returned 409 | mounted + real browser | off | on | off for RC1; 300 s heap sample missed gate and two hours remain unrun |
| Volume pyramids — `FEATURE_VOLUME_PYRAMIDS` | zarr 3.1.x | build/version API tested | no direct user UI | pyramids must be built in staging root | Phase 11 tests | off | on | off unless chunk renderer is approved |
| Chunk service — `FEATURE_CHUNK_SERVICE` | pyramids | token/read API tested | Phase 13 client ready | staging auth and real chunks required | Phase 12/13 tests | off | on | off unless chunk renderer is approved |
| PullQueue — `VITE_FEATURE_CHUNK_PULL_QUEUE` | pyramids + chunk service | n/a | Phase 13 complete | same as chunk service | 42 focused tests | false | true | false unless renderer is approved |
| Chunk renderer — `VITE_FEATURE_CHUNK_RENDERER` | PullQueue + chunk service + pyramids | n/a | Phase 14 complete with TIFF fallback | restored XY/XZ/YZ, token refresh and corrupt-chunk fallback passed | 5 Chromium; 512² warm p95 16 ms; synthetic 2048² p95 166 ms | false | true in separate build | **false**: real 2048² scrub p95 3382 ms versus TIFF 473 ms, and soak gate is incomplete |

EfficientSAM and SAM2 use provider configuration rather than Phase feature
flags. Initial staging retains the production provider choices; their writes
are restricted to the staging data root by both application invariants and the
dedicated OS runtime identity.

The conservative RC1 recommendation is therefore all Phase flags off and both
Vite flags false. This preserves the reviewed TIFF/PNG path. Staging may keep
the backend operation/pyramid/chunk flags enabled for continued private
qualification, but that setting is not a production recommendation.
