# Cross-tile mitochondria stitching

Status: phases 1-7 are implemented (offline management commands), plus
auto-accepting clear-cut merges (`stitch_auto_decisions`) and optional region
(ROI) awareness (see [Regions](#regions-roi)). A volume that was already
stitched upstream is measured with `measure_volume` instead, into the same
delivery files (see
[Measuring an upstream-stitched volume](#measuring-an-upstream-stitched-volume)).
Keep this file current when behavior changes.

## Goal

A large region is delivered as many label tiles. Each tile is segmented on its
own, so one mitochondrion that crosses a tile boundary appears as several
unrelated instance IDs. Stitching tracks each mitochondrion as one object
across tiles, then measures it.

Reference dataset `nag_p10`:

- 80 prediction (label) tiles, HDF5, named like
  `96-352_8192-10240_19456-21504_im_xy.h5`; the matching raw image tiles share
  the box and differ in suffix (for example `..._im.h5`).
- Each tile is 256 x 2048 x 2048 voxels (Z, Y, X) at 30 / 8 / 8 nm. Label `0`
  is background.
- The tiles sit on a sparse 4 x 11 x 10 grid: 80 of 440 positions are present.
  The z slab 864-1120 is missing entirely.
- Phase 1 finds 121 seams: 54 x, 53 y and 14 z.

The real tiles live only on the lab server. Development and tests use synthetic
tiles only.

### Real runs

**First run (phases 1-4) used the 80 nag_p10 prediction tiles:**

- 4,413 candidates on 121 seams.
- 2,704 candidates are one-to-one on their seam with both fractions ≥ 0.5 and
  contact ≥ 50. That is the default `stitch_auto_decisions` rule.
- 113 have contact < 10, or a larger fraction < 0.05.
- The review page had 4,413 cards and 9,842 PNGs, and worked opened from disk
  in Safari and Chrome.

**Input going forward is the proofread labels:**

- 76 tiles are proofread snapshots: uncompressed TIFF, about 2.1 GB each.
- 4 tiles are not proofread yet and use their predictions. List them in
  `--unproofread-tiles` for phase 6.
- Tiles are fed in through a folder of symlinks named by box, for example
  `96-352_..._proofread.tif` and `..._prediction.h5`. Phase 1 reads that folder
  like any tile directory, and the suffixes only become part of the tile names.
- Final runs should use the approved official labels.

Changing the label source changes the label IDs, so rerun every phase from 1.
Decisions from one set of tiles do not carry over to another.

**Region-only run on the proofread labels (phases 1-6, before phase 7):**

- 4,346 mitochondria reach into the region.
- 409 span 2 or more tiles; one spans 10.
- 599 touch a data edge (`touches_edge`).
- 2,246 cross the region boundary.
- 426 touch an unproofread tile.
- 1 was skipped for `crop_limit` (volume kept, no length).
- 50 objects hold two labels from the same tile (`stitch_objects` warns).
  `stitch_validate` classifies them.
- A blind check of 50 randomly sampled auto-accepted pairs agreed 50/50 with
  the auto rule.

**Scope decision (from the scientist): the analysis is region-only.** Only the
region was proofread, so labels outside it may be inaccurate. The deliverable
lists only objects that reach into the region, and flags those that cross its
boundary.

## Decisions

- **Tile names carry the tile box.** A tile name starts with
  `z0-z1_y0-y1_x0-x1` in voxels, half-open, optionally followed by
  `_<suffix>` (for example `96-352_8192-10240_19456-21504_im`). The file
  extension is not part of the tile name. Leading zeros are allowed.
  The box must come first; a name that does not start with it is rejected.
- **Tiles abut without overlap.** Overlaps and duplicate boxes are reported by
  phase 1 and make phases 2 and 3 refuse to run. Gaps and missing grid
  positions are reported but allowed: they only remove seams.
- **Label IDs may restart in every tile.** Nothing assumes they are unique.
- **Global IDs are uint64-range Python integers.** Nothing assumes a tile's IDs
  fit uint16, nor that the global space fits uint32. A total above
  `2**64 - 1` is an error.
- **Read only what is needed.** Phase 3 reads only the two facing planes of
  each seam, never a whole tile. Phase 2 must see every voxel to count labels,
  so it streams each tile in Z blocks (about 64 MB) instead of loading it.
- **Reuse the application's readers.** Tiles are opened with
  `annotation.visualization.slice_io.open_label_volume_readonly` (TIFF memmap,
  HDF5, NIfTI, `.npy`). The one exception is a TIFF that cannot be
  memory-mapped (for example compressed): the shared reader would decode the
  whole file, so `stitching.PagedTiff` reads it one page at a time instead.
  No new dependencies.
- **Arrays are (Z, Y, X)** in the reader's convention, matching the tile name
  order and `annotation.measurements`.
- **Offline only.** No database access, no web API, no ProcessingJob. Inputs
  are never modified.

## Phases

| # | Phase | Status | Output |
|---|-------|--------|--------|
| 1 | Inventory tiles and seams | done | `tiles.csv`, `seams.csv`, `issues.csv` |
| 2 | Globally unique IDs | done | `tile_ids.csv`, `id_map.csv` |
| 3 | Merge candidates at seams | done | `candidates.csv` |
| 4 | Human yes/no proofreading | done | `review/` (offline HTML page) → `decisions.csv`; `image_pairing.csv`; optional `decisions_auto.csv`, `auto_sample_*.csv` |
| 5 | Union-find global object IDs | done | `objects.csv`, `objects_summary.csv`, `decisions_resolved.csv` |
| 6 | Measure stitched objects | done | `measurements.csv`, `unresolved_candidates.csv`, `label_boxes.csv`, `measure_run.json` |
| 7 | Validation and delivery | done | `validate_repeated_tile.csv`, `validate_largest.csv`; `delivery/` (`mitochondria.csv`, `summary.csv`, `excluded.csv`, `README.md`) |

### 1. Inventory

Parse every tile name into a box. Then:

- **Seams.** Two tiles share a face when one's upper bound equals the other's
  lower bound on one axis and their extents overlap with positive area on the
  other two axes. Each seam is canonicalised so `tile_a` is the lower tile and
  `tile_b` lies in the `+axis` direction. The face rectangle is the
  intersection of the two tiles on the other two axes; on a regular grid it is
  the whole tile face.
- **Overlaps.** Any pair of tiles whose boxes intersect with positive volume.
- **Duplicates.** Two entries with the same box, or the same tile name.
- **Grid, gaps and missing positions.** For each axis, the distinct
  `(lo, hi)` intervals of the tiles. If no two intervals on an axis overlap,
  the axis is *regular*; consecutive intervals with `hi < next lo` are gaps.
  When all three axes are regular, every combination of intervals without a
  tile is a missing grid position. An irregular axis is reported and
  missing-position detection is skipped.
- **Shapes.** When a tile's file exists, its array shape is read from the
  header (no voxel data) and compared with the box size.

Seams are numbered in a stable order: by `tile_a` (sorted by `z0, y0, x0`,
then name), then axis `z, y, x`, then `tile_b`.

### 2. Global IDs

Tiles are processed in `(z0, y0, x0, name)` order. Each tile gets an offset:

```text
offset[0]   = 0
offset[i+1] = offset[i] + max_local_id[i]
global_id   = offset[tile] + local_id          (local_id >= 1)
```

Offsets come from each tile's largest local ID, not its label count, so a
global ID converts back to its local ID by subtraction and the ranges of
different tiles never collide. Labels must be integers `>= 0`; float, bool or
negative labels are refused. A tile with no labels contributes nothing to the
offset.

Phase 2 also records each tile file's size and mtime. Phase 3 refuses to run
if a file changed since phase 2, because the ID map would be stale.

### 3. Candidates

For each seam, read tile A's last plane along the seam axis and tile B's first
plane, both cropped to the face rectangle. For every pair of nonzero labels at
the same position, record the number of positions they share
(`contact_voxels`). `area_a` / `area_b` are each label's nonzero area on its
face plane (within the face rectangle); `frac_a = contact_voxels / area_a`
and `frac_b = contact_voxels / area_b`.

A label that ends one voxel before the seam is not on the facing plane, so it
can never be a candidate. Candidates are evidence for phase 4, not decisions:
a pair can touch with tiny contact by chance, and one label can contact several
labels on the other side.

### 4. Proofreading

`stitch_review` builds a folder, `review/`, holding `index.html` and
`crops/*.png`. It needs no server, network or external script, and works opened
from disk (`file://`) in Safari and Chrome. All card data is embedded in the
page as JSON, with IDs as strings because they can exceed 2⁵³.

**Image pairing.** Each prediction tile is paired with the raw image tile that
has the same `z0-z1_y0-y1_x0-x1` box, whatever the suffix after it
(`..._im_xy.h5` ↔ `..._im.h5`). Images come from one or more `--images`
directories, and pairing works as follows:

- Unmatched tiles in either direction are reported on stderr and in
  `image_pairing.csv`; they do not stop the run.
- A card from a tile without a raw image shows that tile as flat gray, with a
  warning.
- Two image files with the same box, or an image whose shape differs from its
  tile, stop the run.

**One card per `candidates.csv` row.** Each card first reads the seam's two
facing label planes (as phase 3 does) to find where `label_a` meets `label_b`,
and checks the count against `contact_voxels`; a mismatch means
`candidates.csv` is stale. It then picks its view:

- **X / Y seam:** one XY slice at the contact slice closest to the contact's
  mean z (ties go to the smaller z). The window is `--crop` pixels square
  (default 160). The seam runs through the middle, and the other axis is
  centred on the contact's mean position at that slice. Pixels on each side
  come from their own tile, so the slice is stitched from both.
- **Z seam:** tile A's last XY slice and tile B's first, side by side, with the
  same window centred on the contact's mean (y, x).

Centres round half up. Windows are never shifted to stay inside the data. The
part outside the two tiles (near a tile or region corner) is drawn as no-data
(dark slate), so crops are clipped rather than failing.

**Images.** One crop pixel is one voxel. The page scales each image to
320 px wide (240 px for each Z slice) with pixelated scaling, which is 2x at
the default crop. Each view is drawn twice:

- **EM:** the raw image alone. uint8 images are shown unchanged; other dtypes
  are stretched between the 0.5th and 99.5th percentile of the card.
- **EM with outlines:** `label_a` outlined in orange, `label_b` in blue, every
  other label's outline blended 40 % toward white. Labels are compared by
  global ID, so equal local IDs in the two tiles never mix. Outlines are not
  drawn along the window edge.

The seam is a dashed yellow line over X/Y views; Z views have a yellow divider
between the two slices. Each card lists seam, axis, tiles, labels, global IDs,
`contact_voxels`, `frac_a` and `frac_b`.

**Reading.** Only these are read: the facing label planes, and the sub-plane of
each slice inside the window, from the label and image tiles. Tiles are never
read whole.

**Controls.**

- Buttons and keys: **Y**es, **N**o, **U**nsure. Deciding moves to the next
  card.
- Arrow keys move between cards. An "Undecided only" filter is available.
- A progress count shows how many cards are decided.
- A reviewer name field fills the `reviewer` column.

Progress is saved in `localStorage`, under a key derived from the
`candidates.csv` contents and shard. Regenerating the page, for example with
another crop, keeps it. **Export** downloads `decisions.csv` with one row per
decided card. **Import** reads a `decisions.csv` to resume or to merge another
reviewer's work: rows for this page replace local decisions, and other rows are
counted and skipped. If storage is unavailable, a warning says to export
regularly.

**Sharding.** `--shard i/n` (1 ≤ i ≤ n) keeps rows `[(i-1)·N/n, i·N/n)` of
`candidates.csv` (integer division). The split is contiguous and
deterministic, so the n shards cover every row exactly once. The default output
folder is `review_<i>of<n>` and the export is named
`decisions_<i>of<n>.csv`. Card IDs (`c000123`) are the candidate's row index
in `candidates.csv`, so they are the same in every shard.

**Subset pages.** `--only FILE` builds a page with only the candidates listed
in `FILE`: any CSV with `seam, tile_a, label_a, tile_b, label_b` columns, such
as an auto-accept sample.

- Rows matching no candidate are refused.
- The default output folder is `review_<file stem>`, and the export is named
  `decisions_<file stem>.csv`.
- `--only` combines with `--shard`; shards split the subset.

**Reviewer name.** The toolbar has a reviewer field. Its value is saved with
progress and stored with each decision. On export, a decision made before a
name was typed gets the current name. Imported decisions keep their own
reviewer, for example the auto rule's name.

### 4a. Auto-accepting clear-cut merges

`stitch_auto_decisions` writes `decisions_auto.csv` in the `decisions.csv`
format. It marks `yes` only for candidates that pass all three tests:

- **one-to-one on their seam:** `label_a` has no other partner on that seam,
  and neither has `label_b`;
- `min(frac_a, frac_b) >= --min-frac` (default 0.5);
- `contact_voxels >= --min-contact` (default 50).

Both bounds are inclusive. Every other candidate is left out, so it stays
undecided. The `reviewer` column records the rule, for example
`auto:one-to-one,min_frac=0.5,min_contact=50`.

**Spot checks.** `--sample N --seed S` also writes
`auto_sample_<n>_seed<S>.csv`, a random sample of the accepted pairs in
candidate order. The same seed and inputs always give the same sample. To check
it:

1. Build a page for just the sample:
   `stitch_review --work W --images I --only W/auto_sample_100_seed1.csv`.
2. Have a reviewer answer it blind. **Do not import `decisions_auto.csv` into
   that page.**
3. Export the answers (`decisions_auto_sample_100_seed1.csv`).
4. Pass both files to `stitch_objects`. Every pair the reviewer answered `no`
   or `unsure` appears as a conflict and stops the run. The conflicts give the
   rule's disagreement count on the sample. Fix them (or tighten the
   thresholds) before continuing.

**Normal use.** To review only what the rule did not accept, import
`decisions_auto.csv` into the full review page, then tick "Undecided only".
Reviewers' exports then include the auto rows with their `auto:` reviewer. Or
pass `decisions_auto.csv` and the reviewers' files to `stitch_objects`
together; agreeing duplicates are fine.

### 5. Objects

`stitch_objects` reads `candidates.csv`, `id_map.csv` and one or more
`decisions.csv` files, which may come from different shards or reviewers.

- Blank decisions count as undecided. Values are matched case-insensitively
  after trimming: `yes`, `no`, `unsure`.
- It refuses, listing every offending row with `file:line`:
  - rows matching no `candidates.csv` row, by seam, tiles, labels and global
    IDs;
  - invalid decision values;
  - pairs given different decisions anywhere in the inputs.

  The same decision repeated is fine.
- It reports counts of yes, no, unsure and undecided. `--require-complete`
  refuses if any candidate is undecided; `unsure` counts as decided but never
  merges.
- Union-find runs over every `global_id` in `id_map.csv` and merges only `yes`
  pairs. `object_id` is the smallest `global_id` in the set, so objects are
  deterministic and an unmerged label keeps its own ID.
- Objects holding two labels from the same tile are listed as a warning. They
  are legitimate only if the object really leaves the tile and comes back.
- It also writes `decisions_resolved.csv`, the decision it used for every
  candidate (`undecided` where none was given). Phase 6 reads it, so phase 6
  needs no decision files.

### 6. Measurement

`stitch_measure` writes `measurements.csv`, one row per object.

**Inputs.** It reads `tiles.csv`, `seams.csv`, `tile_ids.csv`, `id_map.csv`,
`candidates.csv`, `objects.csv` and `decisions_resolved.csv` from the work
directory. It refuses to run if any of these disagree:

- tiles changed since `stitch_ids` (file fingerprint);
- a tile's labels no longer match `id_map.csv` (IDs or voxel counts);
- `objects.csv` does not match the resolved decisions.

**Volume.** `voxel_count` is the sum of the members' `id_map.csv` counts, and
`volume_um3` is `voxel_count · vz·vy·vx / 1e9`. No reading is needed.

**Cable length** reuses the per-volume code unchanged:

- each label's bounding box comes from `label_summary`'s scan
  (`_scan_stats`/`_derive_summary`);
- the crop is that box padded by `CROP_PAD` (2);
- `annotation.measurements.skeleton_length_nm` measures it, with the same
  TEASAR parameters in `em_erl_skel.vol_to_skel`, dust threshold `DUST_SIZE`
  (100), voxel size passed once in (Z, Y, X) nm, and `skel_to_length(...,
  res=[1, 1, 1])`.

No parameter is copied or re-tuned.

Crops are built in **region coordinates**: padded, clipped to the region's
bounding box, and zero outside the object's own voxels. Every object therefore
gets exactly the mask the per-volume code would give it in one untiled volume
of the region. This matters: TEASAR's border handling depends on where the crop
edge is, so a crop clipped at a tile face gives a different length. The tests
check stitched results against `measure_label_volume` on the untiled synthetic
volume.

- **Single-tile objects** (one member; most objects): each tile is read once,
  and every one of its single-tile objects is measured from that read.
- **Multi-tile objects:** after the tile pass, each object's window is the
  union of its members' boxes, padded and clipped. The mask is assembled from
  one read per member tile (the part of the window inside it) and skeletonised
  once as a whole. Summing per-tile lengths would miss the path across seams.
- **Crop limit:** `--max-crop-voxels` (default 512,000,000) caps the padded
  window, for both kinds. An object over the cap keeps `voxel_count` and
  `volume_um3`, but `skeleton_length_um` is empty and `skipped_reason` is
  `crop_limit` (as in the per-volume code).

**Edges.** `touches_edge` means the object touches the **edge of the tiled
volume**, not a seam. **Any tile face with no neighbouring tile counts as a
volume edge.** That includes:

- faces on the region's bounding box;
- faces next to missing grid positions;
- faces next to gaps;
- both faces of the missing z slab 864-1120 in `nag_p10`.

A member label touches an edge when it has a voxel on a tile face at a
position no seam covers. Coverage comes from `seams.csv` face rectangles, so a
partly covered face is checked against the label's own footprint on that face.
Touching a seam face is never an edge, whatever the decision for that pair:
with "no", the neighbour is a different object, not the volume edge.
`edge_faces` lists each `tile:face` (faces `-z`, `+z`, `-y`, `+y`, `-x`, `+x`).

**Bounding box and centroid.** `bbox_z0 … bbox_x1` is the union of the
members' boxes from the pass-1 label scan, in region voxel coordinates
(half-open). `center_z_nm, center_y_nm, center_x_nm` is the centroid: the mean
voxel index on each axis times the voxel size, so voxel `i` is at `i · v` nm.
The coordinate sums are exact integers taken from the mask phase 6 already
assembles for the skeleton (per-axis projections, no extra read). An object
over the crop limit gets no skeleton, but its window is still read once, in Z
slabs of at most `--max-crop-voxels`, for its centroid. Phase 6 checks that the
mask holds exactly `voxel_count` voxels.

Phase 6 also writes `label_boxes.csv` (every label's box, so phase 7 can crop
without a full read) and `measure_run.json` (voxel size, region-only,
crop limit, unproofread tiles, code revision, finish time and the command's
options) for the delivery README.

**Other columns.**

- `unresolved_contacts` counts the object's candidates (either side is a
  member) whose decision is `unsure` or `undecided`. A candidate between two
  unmerged objects counts for both. Every counted candidate is listed in
  `unresolved_candidates.csv` (see below).
- `unproofread_tiles` counts how many of the object's tiles are listed in
  `--unproofread-tiles`: a text file of tile names or paths, matched by box, so
  suffixes may differ.

**Second review round.** `stitch_measure` also writes
`unresolved_candidates.csv`: the candidates counted in `unresolved_contacts`,
in candidate order, with the objects they join. Feed it to
`stitch_review --only` to get a page for exactly those cards. The output goes
to `review_unresolved_candidates/` and exports
`decisions_unresolved_candidates.csv`. Then rerun `stitch_objects` with the
earlier decision files plus the new one, and `stitch_measure` again. The file
has no `decision` column (its `current_decision` is `unsure` or `undecided`),
so importing it into a page decides nothing.

**Progress** is logged per tile (labels, objects measured and skipped,
seconds) and per multi-tile object (members, tiles, crop size, length,
seconds). Then come totals.

### 7. Validation and delivery

Both commands read only the work directory (and, for `stitch_validate`, small
crops of the tiles). Run phase 6 again after upgrading: phase 7 needs
`measurements.csv`'s bbox/centroid columns, `label_boxes.csv` and
`measure_run.json`.

#### 7a. `stitch_validate`

**Consistency (no tile reads).** `stitch_validate` refuses, listing every
violation, unless:

- no global ID is listed twice in `id_map.csv` or `objects.csv`, no object
  twice in `objects_summary.csv` or `measurements.csv`, and `objects.csv`
  covers exactly the labels in `id_map.csv`;
- each object's `object_id` is its smallest member;
- each object's member count and voxel total, in `objects_summary.csv` and
  `measurements.csv`, equal the sums over its members in `id_map.csv` (and
  `region_voxels` too, with region data); the grand totals agree;
- each object's members are connected through `yes` rows of
  `decisions_resolved.csv` alone, and no `yes` row joins two objects.

`stitch_deliver` runs the same checks first.

**Repeated-tile objects.** For every object holding two or more labels from
one tile (`stitch_objects` warns about these), it reads only the union of
those labels' boxes in that tile (from `label_boxes.csv`, in Z blocks of about
64 MB) and records which of the labels share a face (6-connectivity: diagonal
contact does not count).

- `touching`: the labels form one connected group inside the tile. Most likely
  a proofreader split one mitochondrion into two IDs.
- `separate`: they do not. The object leaves the tile and comes back (a U-turn
  through a neighbour), or a `yes` is wrong. Check these visually.

Each (object, tile) pair is one row of `validate_repeated_tile.csv`.

**Unusual objects.** `validate_largest.csv` lists the top `--top` (default 20)
objects by `member_count`, then the top 20 by cable length, for visual
checking. Ties go to the smaller `object_id`. Objects without a length
(`crop_limit`) are left out of the length ranking.

#### 7b. `stitch_deliver`

Writes a delivery folder (default `<work>/delivery`; a non-empty folder needs
`--overwrite`, which replaces only the four files). No tile is read.

- **Scope:** region-only. It refuses `measurements.csv` without region
  columns, and lists only objects with `region_voxels > 0`, whether or not
  phase 6 ran with `--region-only`.
- **Exclusions:** `--exclude-global-ids FILE` lists global IDs (one per line;
  blank lines, `#` comments and a `global_id` header are ignored) that
  reviewers judged not to be mitochondria. Each excludes its whole object,
  which goes to `excluded.csv` with the IDs that caused it, and is left out of
  `mitochondria.csv` and `summary.csv`. Unknown IDs and non-integers are
  refused.
- **`mitochondria.csv`:** `mito_id` is the `object_id` (traceable, not
  renumbered). `cut_off_at_data_edge` is `touches_edge`;
  `touches_unproofread_tile` is `unproofread_tiles > 0`; `length_status` is
  `measured` or `skipped_too_large` (`crop_limit`). `core` is `true` when the
  object is not cut off, does not cross the region boundary, touches no
  unproofread tile and has no unresolved contacts. `not_core_reasons` lists
  the failing ones, in that order: `cut_off_at_data_edge`,
  `crosses_region_boundary`, `touches_unproofread_tile`,
  `unresolved_contacts`.
- **`summary.csv`:** counts for all and core objects, and the median, 90th
  percentile (numpy, linear interpolation) and maximum of volume and of
  measured length.
- **Seam decisions in the README.** Counts come from
  `decisions_resolved.csv` (also restricted to `in_region` candidates when
  known). The auto rule and its count come from `decisions_auto.csv` in the
  work directory, or `--auto-decisions FILE`; every row of it must be `yes`
  in `decisions_resolved.csv`, otherwise it was not the file `stitch_objects`
  used and the run is refused. Human decisions are the decided candidates
  minus the auto-accepted ones. `--spot-check FILE` (repeatable) takes a
  reviewer's blind answers for an auto sample; every row must be an
  auto-accepted pair, and the README reports how many agreed. The real 50/50
  check is recorded only through this option.
- **README provenance:** the code revision of the delivery run and of the
  phase 6 run (`+modified` when the checkout had uncommitted changes), the
  phase 6 command, and every input tile with its file and proofread status.

## Regions (ROI)

**Why.** Annotators proofread mitochondria only inside each tile's region
mask, an ROI of roughly one cell; nonzero means inside. Labels outside the
region are unverified predictions. For now the analysis is region-only: only
mitochondria that reach into the region matter.

Real region files are HDF5 named by box, for example
`96-352_8192-10240_19456-21504_mask_pc2.h5`. They sit in their own
directories, apart from the labels.

Everything below is optional. Without `--regions` every command behaves, and
writes, exactly as before: no new columns or files. This was checked byte for
byte on the synthetic grid, covering every CSV, the review page and its PNGs.
`unresolved_candidates.csv` (phase 6) is written with or without regions.

**Pairing (phase 2).** `stitch_ids --regions DIR` (repeatable) pairs region
files to tiles by the leading `z0-z1_y0-y1_x0-x1` box, exactly as
`stitch_review` pairs images.

- Tiles without a region file are reported on stderr and in
  `region_pairing.csv` (`no_region`), and count as entirely outside the region.
- Region files without a tile are reported too (`no_tile`).
- Two region files with one box, or a region whose shape differs from its tile,
  are errors.
- Rerunning `stitch_ids` without `--regions` drops the region columns and
  removes `region_pairing.csv`.

`--regions` exists only on `stitch_ids`. It is the only phase that reads the
masks. Later phases take region data from `id_map.csv`, and `stitch_review`,
`stitch_auto_decisions` and `stitch_measure` take `--region-only` instead.

**Counting (phase 2).** During the existing full read of each tile, the
region mask is read in the same Z blocks as the labels (about 64 MB each),
so neither is held whole and no two tiles are ever in memory at once.

- `id_map.csv` gains `region_voxels`: each label's voxels inside the region.
- `tile_ids.csv` gains `region_voxels`: the size of the region mask in that
  tile (0 without a region file). Per-label sums are already in `id_map.csv`;
  the region size is not derivable elsewhere and is what densities need.

**Candidates (phase 3).** When `id_map.csv` has `region_voxels`,
`candidates.csv` gains `in_region`: `true` if either label has
`region_voxels > 0`. Without region data the column is left out.

**Region-only review and auto-accept.** `stitch_review --region-only` and
`stitch_auto_decisions --region-only` use only `in_region` candidates. Both
refuse if `candidates.csv` has no `in_region` column.

- **Review page:** default folder `review_region`, export
  `decisions_region.csv`, title "(region only)". Card IDs remain row indices.
- **Auto-accept:** one-to-one is still judged against **all** candidates on
  the seam, and only then restricted to `in_region`. A partner outside the
  region therefore still disqualifies a pair. The rule name gains
  `,region_only`, for example
  `auto:one-to-one,min_frac=0.5,min_contact=50,region_only`.

**Objects (phase 5).** The rules are unchanged. Candidates outside the region
are simply never reviewed and stay undecided, so they never merge.

**Measurement (phase 6).** When `id_map.csv` has `region_voxels`,
`measurements.csv` gains three columns:

- `region_voxels`: the sum over members;
- `frac_in_region`: `region_voxels / voxel_count`;
- `crosses_region_boundary`: `true` when the object has voxels both inside and
  outside the region.

Without region data the columns are left out. `stitch_measure --region-only`
changes three things:

- only objects with `region_voxels > 0` are listed and skeletonised;
- `unresolved_contacts`, and `unresolved_candidates.csv`, count an
  unsure/undecided candidate only if either label's **object** (after the
  phase 5 merges) reaches into the region. Labels alone are not enough.

  Example: A (inside) merged with B (no region voxels) is truncated if B's
  pair with C is undecided, even though neither B nor C is inside. That
  candidate counts for A+B and is listed for review.

  A candidate between two objects that are both entirely outside neither
  counts nor is listed.

Every other column of a listed object is identical to the full run.

## Measuring an upstream-stitched volume

Some regions arrive already stitched and seam-proofread upstream, as one
Neuroglancer volume instead of tiles. `measure_volume` measures such a volume
and writes the same delivery files as `stitch_deliver`, with the same columns
and core rules, so p7 and p10 deliveries line up column for column. It shares
no work directory with phases 1-7.

**p7 (first use).**

- One volume of 10240 × 8192 × 1344 voxels (x, y, z), stitched and
  seam-proofread upstream (who did so is not recorded with the data).
- Labels: `neuroglancer_precomputed`, unsharded, `compressed_segmentation`,
  uint32, 128 × 128 × 64 chunks. The data are sparse: only 3,207 chunk files
  exist.
- Region (the PC cell): a separate precomputed segmentation on the same grid,
  nonzero = inside.
- **Voxel size.** The files' metadata says 32 × 32 × 120 nm (x, y, z), but
  analysis showed the true size is 8 × 8 × 30 nm, the same as p10. So run it
  with `--voxel-size-zyx 30 8 8`.

```bash
python manage.py measure_volume --labels /data/p7/labels --region /data/p7/region_pc \
    --voxel-size-zyx 30 8 8 --work /scratch/measure_p7 --region-only --volume-name p7 \
    2>&1 | tee /scratch/measure_p7.log
```

**Inputs.**

- `--labels DIR`: a local precomputed directory (holding `info`); scale 0 is
  read with tensorstore. It must be unsharded with one channel and an unsigned
  integer type; a sharded scale is refused, because unsharded chunk files are
  what lets the command visit only existing data.
- `--region DIR` (optional): a precomputed mask, nonzero = inside. Its scale 0
  must have the same `size` and `voxel_offset` as the labels, or the run is
  refused. Its chunk size and encoding may differ. A different `resolution`
  in its metadata only warns (and is recorded), since the voxel size is given
  explicitly anyway.
- `--voxel-size-zyx Z Y X` in nm (required). It is always used and the
  metadata resolution never is. When they disagree, the run logs it, and
  `measure_run.json` (`voxel_size_matches_metadata: false`,
  `voxel_size_note`) and the README record it.
- `--work DIR`; `--max-crop-voxels N` (default 512,000,000, commas allowed);
  `--region-only` (needs `--region`); `--exclude-label-ids FILE`;
  `--volume-name NAME` (default: the labels directory name); `--out DIR`
  (default `<work>/delivery`); and `--overwrite`, needed for a non-empty
  delivery folder (replaces only the four delivery files).

**Pass 1: label statistics.** The scale directory is listed, and only chunk
files that exist are read (one chunk-aligned read each, with the next chunk
prefetched). The volume is never read whole.

- A missing chunk is background: Neuroglancer stores no all-zero chunks, and
  tensorstore reads a missing chunk as 0, so pass 2 crops agree with pass 1.
- Any other file in the scale directory (a misaligned box, a `.gz` chunk, a
  stray file) stops the run. Data are never silently skipped. Hidden files
  are ignored.
- Per chunk, labels are compacted with `np.unique` and their boxes found with
  `find_objects` on the compact IDs. Memory and time therefore do not depend
  on how large or sparse the IDs are, unlike the per-volume label scan
  (see [Known limits](#known-limits)). The region mask is read for the same
  box.
- Per label, the merged result is the voxel count, bounding box and region
  voxel count, written to `<work>/label_stats.csv`. Progress is logged every
  5 % with an estimate of the time left.

**Pass 2: measurement.** Each listed label is measured from its own crop
(labels are visited in chunk order, so neighbouring crops reuse decoded
chunks from a 1 GiB tensorstore cache). The crop and length are exactly the
per-volume rule:

- the label's box padded by `CROP_PAD` (2) and clipped to the volume;
- mask = crop == label; `skeleton_length_nm` with the same TEASAR
  parameters, `DUST_SIZE` and voxel size passed once in (Z, Y, X)
  (tensorstore's x, y, z order is transposed at the read);
- over `--max-crop-voxels`, the volume is kept and the length left empty, with
  `skipped_reason` `crop_limit` (`length_status` `skipped_too_large`). The
  window is still read in Z slabs for the centroid.

The crop's voxel count must equal pass 1's, or the run stops (the volume
changed during the run). The centroid uses the same exact integer moments as
phase 6.

**Which labels.** Every label is listed, or with `--region-only` only labels
with region voxels. Excluded labels are not measured.

**Coordinates.** Bounding boxes and centroids use the volume's own voxel
coordinates (Neuroglancer's), including any `voxel_offset`. Voxel `i` is at
`i ·` voxel size nm, as in phase 6.

**Delivery.** The rows go through `stitch_deliver`'s own code: `mito_row`,
`summarise` and the README sections (`readme_*_lines` in
`stitching_deliver.py`). The four files have exactly the columns and metrics
of [7b](#7b-stitch_deliver), and `core` follows the same rule.

- `mito_id` is the label ID in the volume.
- `cut_off_at_data_edge`: the label's box touches one of the six outer faces
  of the volume (the per-volume rule).
- `crosses_region_boundary`: the label has voxels both inside and outside the
  region. `volume_in_region_um3`, `frac_in_region` and
  `crosses_region_boundary` are computed as in phase 6. Without `--region`,
  the first two are empty and the flag is `false`.
- `n_tiles` is `1` and `tiles` is the volume name.
- `touches_unproofread_tile` is always `false`, and the unresolved contact
  count is always 0. The volume was stitched and its seams proofread upstream,
  as a whole. There are no tiles with a recorded proofread status, and this
  command makes no seam decisions, so nothing can be unresolved. `core`
  therefore reduces to "not cut off and not crossing the region boundary".
  The columns are kept so that the files line up with p10.
- `--exclude-label-ids FILE` takes label IDs, in the same file format as
  `--exclude-global-ids` (an optional `label_id` header). Its syntax is
  checked before pass 1, and unknown IDs after it. Each excluded label goes
  to `excluded.csv` (`excluded_global_ids` holds the label ID; the column name
  is shared).
- The README is adapted for an upstream-stitched volume: "stitched and
  seam-proofread upstream", the voxel-size override, why the constant columns
  are constant, and provenance (input paths, scale keys, metadata resolution,
  command, git commit, date, tensorstore version).

**`measure_run.json`** (in the work directory) holds every setting: the
command, `labels` and `region` (path, scale key, size, offset, chunk size,
metadata resolution, encoding, data type), `volume_name`,
`voxel_size_zyx_nm`, `voxel_size_matches_metadata`, `voxel_size_note`,
`region_only`, `max_crop_voxels`, `crop_pad`, the exclusion file and IDs, the
delivery folder, `tensorstore_version`, `code_revision`, `started_at`,
`finished_at`, and counts (chunk files, labels, listed, excluded, core, flags,
skipped, seconds per pass).

`label_stats.csv` — every label from pass 1, by `label_id`, in volume voxel
coordinates (half-open); `region_voxels` only with `--region`:

```text
label_id,voxel_count,region_voxels,bbox_z0,bbox_z1,bbox_y0,bbox_y1,bbox_x0,bbox_x1
```

**Cost.** Pass 1 decodes each existing chunk once (128 × 128 × 64 uint32 is
4 MB). Pass 2 reads each listed label's padded box. The crop cap bounds one
crop at 512 M voxels: 2 GB of uint32 labels plus a 512 MB mask, on top of the
1 GiB cache. Lower `--max-crop-voxels` on smaller machines. Labels are
skeletonised one at a time, single threaded.

**Tests** (`backend/annotation/test_volume_measure.py`) write small synthetic
volumes with tensorstore: compressed_segmentation, 3 × 3 × 3 chunks, a voxel
offset and the wrong 32 × 32 × 120 metadata, plus a raw uint8 region mask with
a different chunk size. Every object's volume, length, edge flag, box and
centroid equal those of `measure_label_volume` on the same in-memory array.
The reference runs on a compact relabelling, because the per-volume scan is
slow with the fixture's 3,000,000,123 ID. The tests cover:

- objects crossing chunk faces, touching volume faces, crossing the region
  boundary, and entirely outside it (left out with `--region-only`);
- a deleted chunk file (background), crop_limit and exclusions;
- the metadata-mismatch record, and a matching-metadata run;
- refusal of a region on another grid, unexpected files and sharded scales;
- column sets identical to `stitch_deliver`'s.

## CLI

All commands are offline, read-only for inputs, and write into a work
directory.

```bash
python manage.py stitch_inventory --tiles /data/nag_p10/labels --work /scratch/stitch_p10
python manage.py stitch_inventory --tiles tiles.txt --work /scratch/stitch_p10
python manage.py stitch_ids --work /scratch/stitch_p10
python manage.py stitch_candidates --work /scratch/stitch_p10
python manage.py stitch_review --work /scratch/stitch_p10 --images /data/nag_p10/images
python manage.py stitch_review --work /scratch/stitch_p10 --images /data/nag_p10/images --shard 2/3
python manage.py stitch_auto_decisions --work /scratch/stitch_p10 --sample 100 --seed 1
python manage.py stitch_review --work /scratch/stitch_p10 --images /data/nag_p10/images \
    --only /scratch/stitch_p10/auto_sample_100_seed1.csv
python manage.py stitch_objects --work /scratch/stitch_p10 --decisions decisions_1of3.csv \
    --decisions decisions_2of3.csv --decisions decisions_3of3.csv --require-complete
```

`--tiles` is either a directory or a text file:

- **Directory:** every non-hidden file with a label extension (`.tif`,
  `.tiff`, `.h5`, `.hdf5`, `.nii`, `.nii.gz`, `.npy`) is a tile. Files with
  those extensions whose names do not parse are reported as `unparsed_name`.
- **Text file:** one tile name or path per line; blank lines and lines starting
  with `#` are ignored. Relative paths resolve against the text file's
  directory. An entry that does not resolve to an existing file is kept as a
  name only (`path` is empty), which is enough for phase 1. A line that does
  not parse is an error.

`stitch_inventory` always writes its outputs and prints the issue counts.
`stitch_ids` reads `tiles.csv`; `stitch_candidates` reads `tiles.csv`,
`seams.csv` and `tile_ids.csv`. Both refuse to run if the inventory has
duplicates or overlaps, a tile has no file, or a tile's shape does not match its
box.

`stitch_review` needs everything `stitch_candidates` needs, plus
`candidates.csv` and one or more `--images` directories (repeatable).

- **Options:** `--crop N` (default 160), `--shard i/n`, `--out DIR` (default
  `<work>/review` or `<work>/review_<i>of<n>`), and `--overwrite`.
- **Existing output:** it refuses a non-empty output folder unless
  `--overwrite` is given, which replaces only `index.html` and `crops/`.
- **Pairing file:** it also writes `<work>/image_pairing.csv`.

`stitch_objects` reads `tiles.csv`, `candidates.csv` and `id_map.csv`, plus
the files given with `--decisions` (repeatable). Add `--require-complete` to
refuse undecided candidates. It writes `objects.csv`, `objects_summary.csv`
and `decisions_resolved.csv`.

`stitch_auto_decisions` reads `candidates.csv`. Its options:

- `--min-frac` (default 0.5) and `--min-contact` (default 50);
- `--out` (default `<work>/decisions_auto.csv`);
- `--sample N --seed S` (default seed 0) and `--sample-out`.

`stitch_measure` reads the work directory and takes:

- `--voxel-size-zyx Z Y X`, in nm and required (nag_p10: `30 8 8`);
- `--unproofread-tiles FILE`;
- `--max-crop-voxels N` (default 512,000,000; commas allowed);
- `--region-only` (see [Regions](#regions-roi)).

It writes `measurements.csv`. A run looks like:

```bash
python manage.py stitch_measure --work /scratch/stitch_p10 --voxel-size-zyx 30 8 8 \
    --unproofread-tiles unproofread.txt 2>&1 | tee /scratch/stitch_p10/measure.log
```

A region-only run passes `--regions` to phase 2 and `--region-only` to the
later commands:

```bash
python manage.py stitch_ids --work /scratch/stitch_p10 --regions /data/nag_p10/regions
python manage.py stitch_candidates --work /scratch/stitch_p10
python manage.py stitch_auto_decisions --work /scratch/stitch_p10 --region-only
python manage.py stitch_review --work /scratch/stitch_p10 --images /data/nag_p10/images --region-only
python manage.py stitch_objects --work /scratch/stitch_p10 --decisions decisions_auto.csv \
    --decisions decisions_region.csv
python manage.py stitch_measure --work /scratch/stitch_p10 --voxel-size-zyx 30 8 8 --region-only
python manage.py stitch_validate --work /scratch/stitch_p10
python manage.py stitch_deliver --work /scratch/stitch_p10 --exclude-global-ids not_mito.txt \
    --spot-check /scratch/stitch_p10/decisions_auto_sample_50_seed1.csv
```

`stitch_validate` takes `--top N` (default 20). `stitch_deliver` takes
`--exclude-global-ids FILE`, `--out DIR` (default `<work>/delivery`),
`--overwrite`, `--auto-decisions FILE` (default `<work>/decisions_auto.csv`
if present) and `--spot-check FILE` (repeatable).

Do not pass `--require-complete` to `stitch_objects` in a region-only run:
candidates outside the region are expected to stay undecided.

`in_region` (candidates, review, auto-accept) is about labels: either label has
region voxels. `stitch_measure --region-only` decides by objects instead, so it
can list unresolved candidates that the first region-only review page did not
include. That happens when a merge pulls an out-of-region label into an
in-region object. Review them with the second round:

```bash
python manage.py stitch_review --work /scratch/stitch_p10 --images /data/nag_p10/images \
    --only /scratch/stitch_p10/unresolved_candidates.csv
```

## File formats

All files are UTF-8 CSV with a header row. Boxes are half-open voxel
coordinates in the whole region. Integers are written in decimal (global IDs
can exceed 64-bit floats' exact range, so never parse them as floats).

`tiles.csv` — one row per tile, sorted by `z0, y0, x0, tile`:

```text
tile,path,z0,z1,y0,y1,x0,x1,grid_z,grid_y,grid_x,shape_zyx,shape_ok
```

`grid_*` is the tile's interval index on that axis (empty if the axis is
irregular). `shape_zyx` is `ZxYxX` from the file header, empty if there is no
file; `shape_ok` is `true`, `false` or empty.

`seams.csv` — one row per shared face:

```text
seam,axis,direction,tile_a,tile_b,position,z0,z1,y0,y1,x0,x1,face_voxels
```

`axis` is `z`, `y` or `x`; `direction` is always `+<axis>` (from `tile_a` to
`tile_b`); `position` is the boundary coordinate (`tile_a`'s upper bound =
`tile_b`'s lower bound). The box columns give the face rectangle on the other
two axes; on the seam axis they are `position, position`.

`issues.csv`:

```text
kind,axis,tile_a,tile_b,z0,z1,y0,y1,x0,x1,detail
```

`kind` is one of `duplicate`, `overlap`, `gap`, `missing_position`,
`irregular_axis`, `shape_mismatch`, `unreadable`, `unparsed_name`, `no_file`.
Unused columns are empty.

`tile_ids.csv` — per tile, from phase 2:

```text
tile,id_offset,max_local_id,label_count,labelled_voxels,dtype,file_size,file_mtime_ns
```

`id_map.csv` — one row per (tile, nonzero local label), sorted by tile order
then `local_id`:

```text
tile,local_id,global_id,voxel_count
```

`candidates.csv` — sorted by `seam`, then `contact_voxels` descending, then
`label_a`, `label_b`:

```text
seam,axis,tile_a,label_a,tile_b,label_b,global_a,global_b,contact_voxels,area_a,area_b,frac_a,frac_b
```

Fractions are written with six decimals.

With region data (`stitch_ids --regions`), these columns are appended:

- `tile_ids.csv`: `region_voxels` (the region's size in the tile);
- `id_map.csv`: `region_voxels` (the label's voxels inside);
- `candidates.csv`: `in_region` (`true`/`false`);
- `measurements.csv`: `region_voxels,frac_in_region,crosses_region_boundary`.

`frac_in_region` is a full-precision float. `crosses_region_boundary` is
`true`/`false`.

`region_pairing.csv` — written by `stitch_ids --regions`, one row per tile,
then one per unused region file:

```text
tile,region_tile,region_path,status
```

`status` is `matched`, `no_region` or `no_tile`.

`image_pairing.csv` — one row per prediction tile, then one per unused image
tile:

```text
tile,image_tile,image_path,status
```

`status` is `matched`, `no_image` (no raw image with this tile's box), or
`no_prediction` (an image tile whose box has no prediction tile; `tile` is
empty).

`decisions.csv` — exported by the review page, one row per decided card:

```text
seam,tile_a,label_a,tile_b,label_b,global_a,global_b,decision,reviewer,decided_at
```

`decision` is `yes`, `no` or `unsure`. `decided_at` is an ISO 8601 UTC
timestamp from the reviewer's browser. Shard pages name their export
`decisions_<i>of<n>.csv`.

`objects.csv` — one row per `global_id` in `id_map.csv`, sorted by
`global_id`:

```text
global_id,object_id
```

`objects_summary.csv` — one row per object, sorted by `object_id`:

```text
object_id,member_count,tiles,total_voxel_count,seam_count
```

`tiles` lists the member tiles in tile order, separated by `;`.
`total_voxel_count` sums the members' `voxel_count`. `seam_count` is the
number of distinct seams with at least one accepted (`yes`) merge inside the
object.

`decisions_resolved.csv` — from `stitch_objects`, one row per candidate in
`candidates.csv` order:

```text
seam,tile_a,label_a,tile_b,label_b,global_a,global_b,decision
```

`decision` is `yes`, `no`, `unsure` or `undecided`.

`decisions_auto.csv` — `decisions.csv` format, one `yes` row per
auto-accepted candidate. `reviewer` is the rule (quoted in CSV, since it
contains commas), and `decided_at` is when the command ran (UTC).

`auto_sample_<n>_seed<S>.csv` — sampled accepted pairs in candidate order:

```text
seam,tile_a,label_a,tile_b,label_b,global_a,global_b,contact_voxels,frac_a,frac_b
```

It has no `decision` column, so importing it into a page decides nothing.

`measurements.csv` — one row per object, sorted by `object_id`:

```text
object_id,member_count,tiles,voxel_count,volume_um3,skeleton_length_um,touches_edge,edge_faces,skipped_reason,unresolved_contacts,unproofread_tiles,bbox_z0,bbox_z1,bbox_y0,bbox_y1,bbox_x0,bbox_x1,center_z_nm,center_y_nm,center_x_nm
```

- The bbox is half-open, in region voxel coordinates; `center_*_nm` are
  full-precision floats (centroid, voxel `i` at `i ·` voxel size).

- `tiles` and `edge_faces` are `;`-separated, in tile order;
  `edge_faces` entries are `tile:face`.
- `volume_um3` and `skeleton_length_um` are full-precision floats (Python
  `repr`). `skeleton_length_um` is empty when `skipped_reason` is
  `crop_limit`.
- `touches_edge` is `true` or `false`.

`unresolved_candidates.csv` — from `stitch_measure`, the candidates counted in
`unresolved_contacts`, in `candidates.csv` order:

```text
seam,tile_a,label_a,tile_b,label_b,global_a,global_b,object_a,object_b,current_decision,contact_voxels,frac_a,frac_b
```

- `object_a` and `object_b` are the objects the two labels belong to after
  phase 5.
- `current_decision` is `unsure` or `undecided`.
- With `--region-only`, only candidates where either object reaches into the
  region are listed.
- `stitch_review --only` accepts the file.

`label_boxes.csv` — from `stitch_measure`, one row per label, by `global_id`;
half-open boxes in region coordinates:

```text
global_id,tile,local_id,z0,z1,y0,y1,x0,x1
```

`measure_run.json` — from `stitch_measure`: `voxel_size_zyx_nm`,
`region_only`, `max_crop_voxels`, `unproofread_tiles` (names, tile order),
`code_revision`, `finished_at` (UTC), `command`.

`validate_repeated_tile.csv` — from `stitch_validate`, by `object_id` then
tile order:

```text
object_id,tile,labels,global_ids,classification,bbox_z0,bbox_z1,bbox_y0,bbox_y1,bbox_x0,bbox_x1
```

`labels` and `global_ids` are `;`-separated; `classification` is `touching` or
`separate`; the bbox is the union of those labels' boxes.

`validate_largest.csv` — from `stitch_validate`:

```text
ranked_by,rank,object_id,member_count,tiles,length_um,volume_um3,bbox_z0,bbox_z1,bbox_y0,bbox_y1,bbox_x0,bbox_x1
```

`ranked_by` is `member_count` or `length`.

`delivery/mitochondria.csv` — from `stitch_deliver`, by `mito_id`:

```text
mito_id,volume_um3,length_um,volume_in_region_um3,frac_in_region,n_tiles,tiles,cut_off_at_data_edge,crosses_region_boundary,touches_unproofread_tile,length_status,bbox_z0,bbox_z1,bbox_y0,bbox_y1,bbox_x0,bbox_x1,center_z_nm,center_y_nm,center_x_nm,core,not_core_reasons
```

`delivery/summary.csv` — `metric,all,core`; metrics `objects, multi_tile,
length_measured, length_skipped_too_large, cut_off_at_data_edge,
crosses_region_boundary, touches_unproofread_tile, unresolved_contacts,
volume_um3_{median,p90,max}, length_um_{median,p90,max}`. Statistics are
empty when there are no values.

`delivery/excluded.csv`:

```text
mito_id,excluded_global_ids,n_tiles,tiles,volume_um3,reaches_region
```

`delivery/README.md` — the scientist's guide; every column above is described
in it.

## Code

- `backend/annotation/stitching.py` — parsing, inventory, ID assignment, plane
  reads, candidates, decisions, union-find, objects, auto-accept, CSV I/O.
- `backend/annotation/stitching_measure.py` — phase 6: face coverage, edges,
  crops in region coordinates, cable length through `annotation.measurements`.
- `backend/annotation/stitching_validate.py` — phase 7a: consistency checks,
  repeated-tile classification (face contacts in Z blocks), largest objects.
- `backend/annotation/stitching_deliver.py` — phase 7b: exclusions, decision
  statistics, the delivery tables and README (its `readme_*_lines` sections
  are shared with `measure_volume`).
- `backend/annotation/volume_measure.py` and
  `management/commands/measure_volume.py` — measuring an upstream-stitched
  Neuroglancer volume (tensorstore reads, chunk listing, both passes, README).
- `backend/annotation/stitching_review.py` — image pairing, card geometry, PNG
  rendering (Pillow), and the review page (HTML, CSS and a small script inlined
  in one template).
- `backend/annotation/management/commands/stitch_inventory.py`,
  `stitch_ids.py`, `stitch_candidates.py`, `stitch_review.py`,
  `stitch_objects.py`, `stitch_auto_decisions.py`, `stitch_measure.py`,
  `stitch_validate.py`, `stitch_deliver.py` — thin CLI wrappers.
- `backend/annotation/test_stitching.py` — synthetic 2 x 2 x 2 grid built in a
  temporary directory, with matching uint8 raw image tiles; no data is
  committed.
  - The page script runs under `node` against a minimal stand-in DOM, covering
    keys, filter, export and import, and the reviewer field across a reload.
    It is skipped where `node` is missing.
  - Phase 6 is checked against `measure_label_volume` on the same objects in
    the untiled synthetic volume, for the grid (including the three-tile
    object C and the G/H1 merge) and a grid of long tubes.
  - Separate small grids cover the edge rule: an empty grid position, a gap
    and a partly covered face.
  - Region masks over part of the grid (one tile without a file) cover the
    region rules. One test checks that a run without `--regions` writes the
    same bytes as before.
  - bbox and centroid are checked exactly against the untiled volume, also
    with every object over the crop limit (slab reads).
  - Phase 7: a two-tile fixture with a touching proofreading split and a
    U-turn; a unit test of face-only contacts across Z blocks; the largest
    lists; each consistency check against a deliberately broken file;
    exclusions; the core flag and reasons on a region layout that produces
    every reason; the README naming every column.

```bash
python manage.py test annotation.test_stitching
```

## Known limits

- A Y or X face plane of a TIFF tile touches every Z page. With a memmap only
  one row per page is copied into memory, but the disk still reads each page;
  compressed TIFFs decode each page once. HDF5 reads only the chunks that
  intersect the plane.
- Phase 2 reads every voxel of every tile once (required to count labels).
- Seam detection is O(tiles²), which is negligible at 80 tiles.
- With `--regions`, phase 2 also reads every region mask once in full, in the
  label blocks. Region masks are not fingerprinted: if one changes, rerun
  `stitch_ids --regions` and every later phase.
- The review page is checked in tests (structure, files, embedded data, and the
  script under node with a stand-in DOM), not in a real browser. Before handing
  pages to reviewers, open one from disk in Safari and in Chrome. Check that a
  decision survives a reload, and that **Export** downloads a file.
- Safari and Chrome may share one `localStorage` for every `file://` page. The
  per-page key keeps different review pages apart, but clearing site data
  clears all of them: export regularly.
- All cards are in one HTML file; images load lazily, only for the card shown.
  Very large candidate lists (tens of thousands) may make the page slow to
  open; use `--shard`.
- **Phase 6 cost.** It reads every tile once in full (label scan for bounding
  boxes), like the per-volume code, then each multi-tile object's window
  again. The default crop cap allows a 512 M-voxel window: about 1 GB of uint16
  labels plus a 512 MB mask in memory for one object. Lower
  `--max-crop-voxels` on smaller machines.
- **Single-object skeletons.** Objects are skeletonised one at a time, single
  threaded (the per-volume code's `num_thread=1`).
- **Phase 7 reads.** `stitch_validate` reads only the union of the boxes of
  each repeated-tile object's labels in that tile; `stitch_deliver` reads no
  tile. Over-limit objects in phase 6 cost one extra read of their window
  (for the centroid).
- **Sparse label IDs.** The label scan reuses the per-volume code's
  `scipy.ndimage.find_objects`, whose memory grows with a slice's largest
  label ID. Tiles with very sparse IDs in the billions would need a different
  scan. Check `tile_ids.csv` `max_local_id` before a phase 6 run.
