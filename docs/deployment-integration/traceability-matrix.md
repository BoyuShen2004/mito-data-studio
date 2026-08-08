# Deployment-to-development traceability

Baseline development HEAD: `25635aed9354041dd7e0b8b5bb62b457d2e0e773`

Audit: `/home/weidf/shenb/mito-deployment-audit-20260731T011322Z`

Recovered commits: `49d350cfc927dc961832d00a062fcfa6b19f35af`,
`145130ea97b79067f70034cb46e5dbef24bb4418`, and
`cb22292c3c8ac7629731ade42530d7c3f461b949`.

“Stronger” means the Phase 12 implementation preserves the recovered
user-visible behavior while adding stricter ownership, compatibility,
autosave, recovery, or permission guarantees. Generated output, live
configuration, and production data are deliberately outside this matrix.

| Recovered behavior | Deployment source / commit | Development implementation | Final status | Automated evidence | Action and omission risk |
|---|---|---|---|---|---|
| Reopen TIFF after atomic replacement in another worker | `annotation/visualization/slice_io.py`, `49d350c` | inode/device/size/mtime cache identity combined with Phase 12 ownership guards | implemented and tested | `test_cellable_port.py`, `test_slice_io_runtime.py` | semantic reimplementation; critical stale-save risk |
| Missing or unknown TIFF/OME units do not invent physical spacing | `core/utils.py`, `49d350c` | strict unit parser, including ImageJ spacing without truthy fallback | implemented and tested | `core/test_utils.py` | semantic reimplementation; high geometry risk |
| Working-copy-first whole-volume operations | `annotation/services.py`, `49d350c` | Phase 10 working-label ownership and operation services | development stronger | annotation operation, tracking, ownership suites | retained; critical source-label risk |
| Invalid slice rejection | deployment API behavior, `49d350c` | Phase 10 bounds validation on read/write APIs | development stronger | API flow and data-root tests | retained |
| Save single-flight and stale-read rejection | `AnnotationCanvas.tsx`, `145130e` | Phase 10 save coordinator, revisioned reads, per-slice dirty state | development stronger | mounted autosave, revisioned-fetch, pending-buffer tests | retained; critical edit-loss risk |
| Autosave, recovery, refresh and resume | deployment editor behavior, `145130e` | Phase 10 IndexedDB recovery and lifecycle coordinator | development stronger | mounted editor and autosave lifecycle tests | retained |
| Large-raster undo memory ceiling | `sliceHistory.ts`, `145130e` | 160 MiB live/parked budget with per-plane entry limit | implemented and tested | `sliceHistory.test.ts` | semantic reimplementation; high OOM risk |
| Deployment no-copy pending buffer | `pendingSliceBuffer.ts`, `145130e` | snapshot isolation retained; redundant history copy removed separately | development stronger | pending-buffer race/revision tests | do not port unsafe aliasing |
| Box and Point tool switching | deployment canvas/chrome, `145130e` | Phase 10 explicit paint-tool state | development stronger | editor/tool backend suites and frontend build | retained |
| Track and Labels behavior | `TrackRail.tsx`, `LabelsPanel.tsx`, `145130e` | full reset already present; Track now blocked outside axial view | implemented and tested | typecheck/build; tracking API tests | ported missing axial guard |
| Delete, merge and split refinements | annotator chrome, `145130e` | byte-identical delete chrome plus Phase 10 operation services | development stronger | annotation operation tests | retained |
| Axis switching | deployment canvas/axis helpers, `145130e` | Phase 10 axis-aware cache keys, edit guards and coordinate transforms | development stronger | axis/tool tests and frontend build | retained |
| Large-plane 2000% detail compensation | `axisView.ts`, `145130e` | identical `detailPreservingZoom` implementation | already identical | `axisView` behavior plus typecheck/build | already covered |
| Labels full reset | `LabelsPanel.tsx`, `145130e` | identical full filter/visibility/pin reset | already identical | frontend typecheck/build | already covered |
| Range-slider endpoint layout | `styles.css`, `145130e` | range-specific CSS reset | implemented and tested | production build | ported; low UI risk |
| Unified authenticated binary API transport | deployment viewer clients, `145130e` | central JSON/blob/ArrayBuffer transport with proxy error preservation | implemented and tested | `api/client.test.ts` | semantic reimplementation; high auth/routing risk |
| EfficientSAM prompt-centred ROI | `ai/prompt_roi.py`, `49d350c` | bounded snapped ROI, full-frame RLE remap, ROI cache identity | implemented and tested | `ai/test_prompt_roi.py`, `test_cellable_port.py` | ported through application boundary; high large-frame risk |
| Prompt-location ROI warming | warm API/canvas, `49d350c`/`145130e` | slice warm plus debounced cursor-region warm | implemented and tested | API tests, frontend typecheck/build | semantic reimplementation |
| EfficientSAM CUDA encoder with CPU fallback | `ai/efficient_sam.py`, `49d350c`/`cb22292` | optional CUDA provider selection, CPU fallback | implemented and tested | AI unit suite; hardware smoke remains environment-dependent | code preserved; GPU packages remain optional |
| Best-IoU EfficientSAM candidate | `ai/efficient_sam.py`, `49d350c` | selects maximum predicted IoU rather than fixed candidate | implemented and tested | EfficientSAM mocked inference tests | ported |
| Cross-process embedding single-flight | `ai/embed_cache.py`, `49d350c` | ROI-keyed disk cache and `flock` guard | implemented and tested | cache/concurrency tests | ported; medium duplicate-GPU-work risk |
| SAM2 XY crop | `tracking/xy_crop.py`, `49d350c` | seed-union crop, paste and border expansion | implemented and tested | `tracking/test_xy_crop.py` | ported; high large-frame risk |
| Shared multi-object SAM2 propagation | `tracking/adapters/sam2.py`, `49d350c` | one sequence with stable object IDs and seed validation | implemented and tested | tracking provider tests | ported; high identity-corruption risk |
| Immutable three-tier region-mask model | projects/volumes models and migrations, `49d350c` | new non-colliding Phase 12 migrations and read-only source/reference paths | implemented and tested | project, volume, API and immutability tests | semantic reimplementation; critical data risk |
| Three-tier scan/register workflow | volume services/API, `49d350c`; registration UI, `145130e` | raw/region/editable matching, shape checks and explicit UI | implemented and tested | backend API flows, frontend typecheck/build | ported |
| Region overlay below editable labels | visualization and viewer, `49d350c`/`145130e` | separate read-only stream, cache and opacity control | implemented and tested | region API tests, frontend build | ported |
| Region-mask visualization contracts | in-app/neuroglancer visualization adapters, `49d350c` | viewer state and adapter fields preserved | implemented and tested | provider/API suites | ported |
| Stateless public full-task sharing | task-share services/API/UI, `49d350c`/`145130e` | signed token, anonymous read-only APIs and shared viewer route | implemented and tested | backend permission tests, `TaskSharePage.test.tsx` | ported; high permission risk |
| Public share cannot create a working label | public read services, `49d350c` | existing working label or official label selected read-only | implemented and tested | public task/hard-case tests | semantic strengthening |
| Legacy public 3D compatibility route | deployment removed it, `49d350c` | route retained beside mesh transport | development stronger | 3D API tests | retained compatibility |
| Hard-case workflow | deployment live workflow; frontend WIP at baseline | existing backend permission/revoke/status APIs plus reviewed inbox/detail UI | implemented and tested | `test_tracking.py`, frontend build | WIP preserved and made part of committed source |
| People workflow | deployment live workflow; frontend WIP at baseline | role-scoped backend overview/profile/detail plus reviewed routes/UI | implemented and tested | `accounts/test_people.py`, frontend build | WIP preserved and made part of committed source |
| Login, resume and project navigation | deployment pages, `145130e` | Phase 10–12 routing, auth and task lifecycle architecture | development stronger | accounts, task, mounted recovery tests | retained |
| Deployment identity diagnostics | operational behavior, `cb22292` | Phase 12 identity endpoint/headers and runbooks | development stronger | identity tests and system checks | retained |
| CUDA wheel selection and live values | `environment.yml`, `.env`, `cb22292` | AI providers are optional; live values remain release configuration | deployment-only configuration intentionally excluded | CPU suite and import checks | do not force CUDA on CPU installs |
| Generated bundles, caches and runtime state | `frontend/dist`, caches, logs, PID files | rebuilt from reviewed source only | generated/runtime artifact intentionally excluded | production build | never port generated output |
| Legacy API/helper removals | `labels_3d.py`, lifecycle/permissions, `49d350c` | compatibility kept alongside new architecture | development stronger | compatibility and permission suites | obsolete deletion not ported |
| Cosmetic login/proofreading copy and fixed rail widths | pages/styles, `145130e` | current responsive Phase 12 copy/layout retained | development stronger | frontend build | no user-visible capability omitted |

## Dependency and data decisions

- ONNX/CUDA support remains optional. This preserves the recovered GPU path
  without making CPU installations depend on CUDA wheels.
- Source images, official labels, region masks, uploads, production databases,
  `.env`, logs, caches, `frontend/dist`, and `staticfiles` are not Git inputs.
- The stateless share-token lifetime matches the recovered deployment. A future
  revocation/expiry model is a product enhancement, not a missing recovered
  capability.
