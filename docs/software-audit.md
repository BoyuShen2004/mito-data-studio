# Software audit — 2026-10-02

Scope: application-wide frontend copy review, plus targeted inspection of metadata,
async loading, review queues and processing jobs. This is not a comprehensive
security audit or a measured load test. Changes stay on `feature/measure-mito`.

## Implemented

- Removed redundant page introductions and shortened measurement, collaboration,
  review and viewer explanations. Kept action labels, routes, controls and workflows.
- Retained consequential information: units, label source, whole-volume scope,
  stale results, Save versus Submit, approval, read-only access and destructive actions.
- Stabilized `useAsync.reload` so effects can depend on it without timer churn.
- Display unknown QC file size as `—`, preserving actual zero.
- Fixed voxel autodetection to fill only missing axes, preserving registered values
  even when another axis is missing. Added regression coverage for all three axes.
- Updated copy-dependent tests; the canvas Save test now selects the accessible
  action instead of depending on explanatory tooltip text.

No login, authorization, reset, feature flags, task lifecycle or source-image changes.
No schema migration or existing-data rewrite.

## Next improvements, in priority order

| Priority | Finding and location | Proposed internal change |
| --- | --- | --- |
| High | Native processing executes inside the dispatcher after a job is claimed (`backend/processing/services.py`). A killed process can strand a submitted job; local polling has no recovery lease. | Add worker leases/heartbeats and explicit interruption handling. Recover only after checking idempotency and input fingerprints; test process termination. |
| Medium | Home loads full tab lists; pending submissions are converted to task rows (`frontend/src/pages/HomePage.tsx`). Multiple submission channels can represent the same task. | Define queue identity/count semantics explicitly and test both channels together; retain separate submission review decisions. |
| Medium | Some list summaries derive zero from missing/loading data. | Represent loading, unavailable and actual zero separately using a shared async-summary primitive, without changing navigation. |
| Medium | Default API pagination is disabled (`backend/config/settings.py`); nested task histories can make lists expensive. | Profile query count and payload size, retain required task prefetches, then introduce compatible pagination and separate aggregate counts. |
| Medium | Viewer canvas and annotation API/services are very large modules. | Extract rendering, edit history, persistence and review logic behind existing interfaces, one regression-tested boundary at a time. Keep controls in place. |
| Medium | Physical spacing crosses a µm storage / nm measurement boundary. | Centralize typed spacing and conversion helpers; test anisotropic and missing values. Never infer scientific units from numeric magnitude. |

These architectural changes are recommendations, not part of this deployment.
