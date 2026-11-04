# Cross-tile mitochondria stitching

Status: phases 1-3 are implemented (offline management commands). Phases 4-7
are planned and described here so a later session can continue from this file.
Keep it current when behavior changes.

## Goal

A large region is delivered as many label tiles. Each tile is segmented on its
own, so one mitochondrion that crosses a tile boundary appears as several
unrelated instance IDs. Stitching tracks each mitochondrion as one object
across tiles, then measures it.

Reference dataset `nag_p10`: 80 label tiles of 256 x 2048 x 2048 voxels
(Z, Y, X) at 30 / 8 / 8 nm. Label `0` is background.

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
| 4 | Human yes/no proofreading | planned | offline HTML review page → `decisions.csv` |
| 5 | Union-find global object IDs | planned | `objects.csv` |
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

### 4. Proofreading (planned)

Generate a self-contained offline HTML page (no server, no external assets)
listing candidates per seam with the two facing planes cropped around the
contact, both labels highlighted, and the contact numbers. A reviewer answers
yes/no per candidate; the page exports `decisions.csv`
(`seam, label_a, label_b, global_a, global_b, decision, reviewer, decided_at`).
Possible auto-accept rule to discuss before implementing: very high
`frac_a` and `frac_b` with a single partner on each side.

### 5. Union-find (planned)

Start with every `global_id` from `id_map.csv` as its own set and union the
pairs accepted in `decisions.csv`. Output `objects.csv`
(`object_id, global_id, tile, local_id`). `object_id` should be deterministic,
for example the smallest member `global_id`. Report objects spanning many
tiles for spot checks.

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

`touches_edge` means the object touches the **outer boundary of the whole
tiled region**, not a seam. A tile face that is neither a seam nor on the
region's bounding box (a gap or missing tile) is not inside the region either;
treat contact there as touching the edge and report it separately so it can be
reviewed.

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

## Code

- `backend/annotation/stitching.py` — parsing, inventory, ID assignment, plane
  reads, candidates, CSV I/O.
- `backend/annotation/management/commands/stitch_inventory.py`,
  `stitch_ids.py`, `stitch_candidates.py` — thin CLI wrappers.
- `backend/annotation/test_stitching.py` — synthetic 2 x 2 x 2 grid built in a
  temporary directory; no data is committed.

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
