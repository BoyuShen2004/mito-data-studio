# RC2 feature-flag recommendation

The production recommendation remains conservative: preserve the reviewed
TIFF/PNG behavior and leave every transformation flag at its disabled default.
Staging qualification with selected backend annotation features enabled does
not change that default.

| Feature / flag | Dependencies | Release evidence | RC2 staging qualification | Production recommendation |
|---|---|---|---|---|
| Teams — `FEATURE_TEAMS` | none | backend permissions/tests | off | off; administration/backfill is outside this release |
| Task hierarchy — `FEATURE_TASK_HIERARCHY` | teams for policy use | backend model/API tests | off | off; no legacy backfill decision |
| Pull claim — `FEATURE_TASK_CLAIM` | task hierarchy | PostgreSQL concurrency tests | off | off; no release UI qualification |
| Auto-fill — `FEATURE_AUTO_FILL_SCHEDULER` | task hierarchy | service/CLI tests | off | off; no restored-row qualification |
| Review history — `FEATURE_REVIEW_HISTORY` | none | backend migration/API tests | off | off for live parity |
| Dashboards — `FEATURE_DASHBOARDS` | none | read API tests | off | off; no complete release client |
| Annotation operations — `FEATURE_ANNOTATION_OPS` | none | backend, mounted UI, restored Save/conflict | on privately | off for initial public cutover |
| Interpolation — `FEATURE_INTERPOLATION` | operations | golden/backend/mounted tests | on privately | off for initial public cutover |
| Annotation tools — `FEATURE_ANNOTATION_TOOLS` | operations | Box/Point/fill/tool and restored UI checks | on privately | off for initial public cutover |
| Autosave/recovery — `FEATURE_AUTOSAVE_RECOVERY` | operations for deltas | versioned Save/reload, recovery, 409 conflict | on privately | off for initial public cutover; qualify separately after observation |
| Volume pyramids — `FEATURE_VOLUME_PYRAMIDS` | Zarr v3 | Phase 11 and restored pyramids | on privately | off unless the chunk stack is later approved |
| Chunk service — `FEATURE_CHUNK_SERVICE` | pyramids | Phase 12 token/read and restored chunk tests | on privately | off unless the chunk stack is later approved |
| PullQueue — `VITE_FEATURE_CHUNK_PULL_QUEUE` | pyramids + chunk service | Phase 13 scheduler/client tests | false in TIFF build | false |
| Chunk renderer — `VITE_FEATURE_CHUNK_RENDERER` | PullQueue + service + pyramids | pixel/browser correctness, but restored 2048² performance miss | false in release build | **false** |

EfficientSAM and SAM2 are provider selections rather than Phase flags. Their
three required model objects have a protected, hash-verified offline bundle;
application writes remain restricted to the selected data root.

Any later enablement is a separate operational change with its own restored
data, browser, multi-user, fallback, and rollback gate. In particular, backend
chunk availability does not imply that the compile-time renderer flag may be
enabled.
