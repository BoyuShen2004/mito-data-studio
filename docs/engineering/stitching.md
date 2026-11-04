# Cross-tile mitochondria stitching

Status: phases 1-5 are implemented (offline management commands). Phases 6-7
are planned and described here so a later session can continue from this file.
Keep it current when behavior changes.

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
| 4 | Human yes/no proofreading | done | `review/` (offline HTML page) → `decisions.csv`; `image_pairing.csv` |
| 5 | Union-find global object IDs | done | `objects.csv`, `objects_summary.csv` |
| 6 | Measure stitched objects | planned | `measurements.csv` |
| 7 | Validation and delivery | planned | report + archived work directory |

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

### 6. Measurement (planned)

Measure each stitched object: voxel count and volume summed across its
pieces, cable length with the existing measurement code
(`annotation.measurements` / `annotation.third_party.em_erl_skel`: call
`vol_to_skel(res=...)` once, then `skel_to_length(..., res=[1, 1, 1])`; voxel
size `(30, 8, 8)` nm in `(vz, vy, vx)`). Cable length needs one skeleton of the
whole object, so the object's pieces must be assembled into one global crop
(the object's union bounding box) before skeletonising; summing per-tile
lengths would miss the edges across seams. Oversized crops are skipped and
flagged with `skipped_reason="crop_limit"`, never fail the run.

`touches_edge` means the object touches the **outer boundary of the tiled
volume**, not a seam. **Any tile face with no neighbouring tile counts as a
volume edge.** That includes faces on the region's bounding box, faces next to
missing grid positions, and both faces of the missing z slab 864-1120 in
`nag_p10`. An object touching any such face is flagged. A tile face is
interior only where `seams.csv` lists it, and only within that seam's face
rectangle.

### 7. Validation and delivery (planned)

Check voxel totals are conserved (sum over objects equals sum over `id_map`),
every accepted merge is in exactly one object, and no object contains two
labels from the same tile unless a decision chain links them. Spot-check a
sample of stitched objects visually. Deliver the CSVs with the command lines,
code revision and voxel size used.

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
refuse undecided candidates. It writes `objects.csv` and
`objects_summary.csv`.

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

## Code

- `backend/annotation/stitching.py` — parsing, inventory, ID assignment, plane
  reads, candidates, decisions, union-find, objects, CSV I/O.
- `backend/annotation/stitching_review.py` — image pairing, card geometry, PNG
  rendering (Pillow), and the review page (HTML, CSS and a small script inlined
  in one template).
- `backend/annotation/management/commands/stitch_inventory.py`,
  `stitch_ids.py`, `stitch_candidates.py`, `stitch_review.py`,
  `stitch_objects.py` — thin CLI wrappers.
- `backend/annotation/test_stitching.py` — synthetic 2 x 2 x 2 grid built in a
  temporary directory, with matching uint8 raw image tiles; no data is
  committed.
  - The page script runs under `node` against a minimal stand-in DOM, covering
    keys, filter, export and import. It is skipped where `node` is missing.

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
