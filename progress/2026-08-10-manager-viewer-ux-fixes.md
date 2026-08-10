# Manager/viewer UX fixes — 2026-08-10

Implemented the ten-item manager/viewer brief in the main tree.

## What changed

- Project Access and working-team assignment now share one roster service;
  `sync_project_rosters` previews legacy gaps and applies only with `--apply`.
- Verified metadata adopts legacy sidecars even when the mask was already
  migrated, and working mask/sidecar mutations serialize per volume.
- Jump-to-region revalidates on mount/axis change, checks the returned axis,
  bounds and de-duplicates indices, and exposes the mask revision.
- Watershed falls back from a distant-ID global AABB to a validated seed-local
  crop; Seeds no longer shows Active/New and new IDs remain backend-assigned.
- Region-only Split/Watershed results preserve their exact outside-ROI extent
  on Save and remain visible until the Region-only session is toggled.
- Labels rows have Verify/Unverify context actions; canvas rows can toggle the
  same 3D pin set as Labels.
- Shortcut profile PATCHes lock per account. Slice/lifecycle/whole-volume
  writes serialize per working volume, and stale slice saves return HTTP 409.
- Hard cases accept and display an optional 1,000-character note.
- Annotate scrub coalescing changed from 100 ms to 16 ms; directional prefetch
  changed from `+1,-1,+2` after 250 ms to five ahead/one behind after 35 ms;
  image/RLE caches increased from 16/64 to 48/128 entries.
- Verified is now an explicit geometry lock across brush/erase, local and
  server tools, whole-volume operations, direct lifecycle deletion, and raw
  slice PUTs. Changing it requires Unverify first; Hide Verified cannot make a
  hidden instance paintable.
- Verify flushes pending geometry first and rejects ids absent from the saved
  working volume. A failed Labels refresh retains the last known protection
  state instead of clearing it.
- Lifecycle sidecars are atomically persisted with a checksummed,
  same-generation backup; a corrupt or semantically tampered primary recovers
  from that backup, while two invalid copies fail closed without overwriting
  the evidence. Reset removes both copies.
- Approval/reset are documented as intentional checkpoint boundaries: they
  install/reseed geometry and begin a fresh verification lifecycle rather than
  silently pretending an old verification still describes new work.

## Scrub measurement method

Use browser Performance recording with network cache disabled for a cold pass
and enabled for a warm pass. Hold D for at least 50 layers, then A for the same
range. Mark keydown-to-image `load`/overlay paint and report median, p95, and
the longest pause. Repeat three times in View and Annotate on the same volume.

The deterministic scheduling floor measured from the old/new constants is
100→16 ms for foreground coalescing and 250→35 ms before neighbour prefetch
(84% and 86% lower respectively). Real end-to-end numbers remain deployment
and dataset dependent; record them with the method above rather than claiming
synthetic timings as network/render measurements.

The existing controlled `npm run bench:phase13` harness also passed after the
change. In that run, warm p95 ranged from 0.36–3.52 ms for the 512-pixel
fixtures and 1.17–26.28 ms for the 2048-pixel fixtures. This is a synthetic
chunk-cache guard, not a substitute for the 50-layer browser measurement above;
there was no representative deployed volume/browser available in this worktree
for an honest end-to-end before/after capture.

## Manual verification

Follow the checklist in
`docs/codex-prompts/2026-08-10-manager-viewer-ux-fixes.md`. In particular, test
Access and People additions in both directions, reopen a verified label, save
a Region-only Split/Watershed, exercise both context menus, open a noted hard
case as a member and via its public link, and force a same-task two-tab save
conflict. The backfill command was **not run** and no production data changed.

## Automated QA

- Frontend: full Vitest suite, **431 passed**; production Vite build passed.
- Backend geometry/tool suites: **195 passed** (interpolation, flood fill,
  tracking, whole-volume operations, reset, and related APIs).
- Backend account/roster/region/lifecycle suites: **153 passed**.
- Focused sidecar corruption/lock regressions: **4 passed**.
- `manage.py check`, `makemigrations --check --dry-run`, and
  `git diff --check` passed. The existing test-only jsdom `scrollTo`, React
  `act(...)`, missing `staticfiles/`, and CPU ONNX-provider warnings remain
  non-failing.
