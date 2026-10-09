# Trace a feature: Measurements

This example connects UI controls to backend computation. Read the
[user workflow](../user-guide/09-measurements.md) first, then follow these files.

## 1. Page and inputs

[ProjectDetailPage.tsx](../../frontend/src/pages/ProjectDetailPage.tsx) mounts the
rightmost **Extensions** tab. The [extension registry](../../frontend/src/features/extensions/registry.ts)
lists Measurements and lazily loads its [adapter](../../frontend/src/features/extensions/measurements/extension.tsx)
when selected. The adapter receives shared project and volume context from
[ProjectExtensions.tsx](../../frontend/src/features/extensions/ProjectExtensions.tsx).
See [adding a project extension](extensions.md) to reuse this host for another
research tool without adding a core tab.
[ProjectMeasurements.tsx](../../frontend/src/components/ProjectMeasurements.tsx)
handles volume selection and voxel-size inputs;
[MitoMeasurements.tsx](../../frontend/src/components/MitoMeasurements.tsx)
handles source selection, queueing, status and results.

Browser state is distinct from database records. When switching volumes, stale
asynchronous responses must not replace the newly selected volume's state.

## 2. Browser API requests

[api/measurements.ts](../../frontend/src/api/measurements.ts) uses the common client:

| Request | Effect |
| --- | --- |
| `GET /api/volumes/:id/measurement-spacing/` | Reads available physical spacing without saving metadata |
| `GET /api/volumes/:id/measurements/?source=official` | Reads the latest job/result for that source |
| `POST /api/volumes/:id/measurements/` | Queues a run; skeletonization does not execute inside the HTTP request |

Find these routes in [config/urls.py](../../backend/config/urls.py), which leads to
[measurement_api.py](../../backend/annotation/measurement_api.py). Read endpoints
require authentication and volume-view access. Run requests require a manager.
UI controls do not replace backend authorization.

## 3. Data and computation

- [measurement_spacing.py](../../backend/annotation/measurement_spacing.py): preserves registered spacing per axis, then supplements missing axes from readable file metadata. Missing calibration stays unknown.
- [processing/models.py](../../backend/processing/models.py): persists processing-job metadata and status.
- [processing/services.py](../../backend/processing/services.py): the dispatcher claims jobs and invokes the appropriate runner.
- [measurement_jobs.py](../../backend/annotation/measurement_jobs.py): selects inputs, checks file/spacing changes and stores derived results.
- [measurements.py](../../backend/annotation/measurements.py): computes per-ID voxel counts, physical volume and skeleton cable length.

Stored spacing is in µm; the form and measurement engine use nm. Convert at the
measurement boundary instead of storing nm values as µm. Measurement API jobs
select the local processing backend and execute via the dispatcher's native
Python runner, separately from interactive SAM2 inference.

A queued job will not run until a dispatcher accepts it with the required
dependencies installed. See the [measurement prerequisites](measurements.md#deployment-prerequisites)
and [audit](../research/documentation-audit.md#ambiguities-and-suspected-implementation-bugs-no-runtime-changes)
for deployment and interrupted-job recovery limits.

## 4. Follow the regression tests

| Test | Behavior covered |
| --- | --- |
| [ProjectMeasurements.test.tsx](../../frontend/src/components/ProjectMeasurements.test.tsx) | Volume selection and spacing detection/save |
| [MitoMeasurements.test.tsx](../../frontend/src/components/MitoMeasurements.test.tsx) | Explicit queueing, job status and result presentation |
| [test_measurement_api.py](../../backend/annotation/test_measurement_api.py) | API authorization, queueing and input changes |
| [test_measurement_spacing.py](../../backend/annotation/test_measurement_spacing.py) | Axis order, units and missing values |
| [test_measurements.py](../../backend/annotation/test_measurements.py) | Volume and length for known synthetic shapes |

Exercise: trace why opening the page does not start computation and why
browser-only pending edits cannot be measurement inputs. Edits already persisted
by explicit Save or autosave are part of the saved working source. Answer these
questions before changing computation. Synthetic test success does not establish
biological accuracy on real research samples.
