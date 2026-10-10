# Project Extensions

Open a project and choose **Extensions**, the rightmost tab after the sections
available to your role. This catalog lists research tools enabled in this
deployment. Select **Open Measurements** to use the current tool. Future
registered tools appear here without adding project tabs.

## Access and workflow

Authenticated users who can view the project can open its catalog. Each card
describes what their role can do. Opening a tool grants no additional data
access or permissions. Public sharing pages do not expose the catalog.

- Managers, including assistants in the Manager workspace, can select a volume and source, save voxel size, explicitly start
  measurements, inspect results and export CSV.
- Annotators (including dual-role users in Annotator mode) and requesters with volume-view access can inspect results and
  export CSV. A manager must save voxel size or start a new run.
- Select **All extensions** to return to the catalog, or choose a core project
  tab to continue registration, assignment or review.

Opening the catalog or Measurements starts no processing job. Measurement runs
require **Run measurements**; saving spacing requires **Save voxel size**.
Neither action edits labels, submits work or makes a review decision. See
[Measurements](09-measurements.md) for inputs, scientific limits and recovery.

Leaving Measurements unmounts its workspace. Save intentional voxel-size changes
before switching sections; browser-only form input is not retained after leaving.
Queued jobs continue independently of the page. Reopen the same volume and
source to inspect status and stored results. Volume-detail **Measurements**
shortcuts open this extension with that volume selected. Old project Measurements
links continue to work.

## If a tool is unavailable

An empty catalog means no extensions are enabled. An unavailable message may
mean an old link names a disabled or removed tool. Select **All extensions** to
see current choices; ask the maintainer if a required tool is missing. There is
no user-facing install or enable switch.

If a workspace cannot load, return to the catalog or another project tab.
Reload the page to retry loading. API and processing failures are handled within
each tool; follow its recovery instructions. Disabling a catalog entry does not
delete saved results or cancel jobs.

[User guide](../user-guide.md) · [Developer guidelines](../engineering/extensions.md)
