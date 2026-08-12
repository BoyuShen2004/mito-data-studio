"""Whole-volume label summary + 3D surface meshes for the 3D Labels panel.

Not a direct Cellable port (Cellable's desktop app has the whole label
volume in RAM already, via ``updateUniqueLabelListFromEntireMask`` for the
label list and ``VTKSurfaceWidget`` for a real marching-cubes iso-surface
render) — the web app must never load a whole EM label volume per request
(same reasoning as ``annotation/visualization/slice_io.py``), so everything
here reads the working label file as a memmap and caches its (potentially
expensive, O(volume)) results, invalidated by the file's mtime.

**3D rendering choice**: :func:`labels_3d_mesh` now produces the same *kind*
of geometry Cellable's VTK widget does — a marching-cubes iso-surface per
label — instead of the block-max-pooled "surface voxels drawn as instanced
cubes" preview this module used to serve (which read as a stack of 2D voxel
slabs rather than a mitochondrion, see
``progress/history/03-fix-hard-case-share-view.md`` item B). The volume-scale
problem is solved the same way as before — crop to the label's own bounding
box and pool it down so the meshed grid stays small — but the pooling is now
a *mean* pool, which turns the binary mask into an occupancy field in [0, 1];
a light gaussian blur plus ``marching_cubes(level=0.5)`` over that field
yields a smooth closed surface. ``labels_3d_preview`` (the old voxel-grid
form) is kept for the legacy ``labels-3d`` endpoint.

**Per-label independence**: every mesh is computed and cached in its *own*
bounding box, in whole-volume voxel coordinates. Two consequences that
matter: pinning one more label in the 3D panel re-uses every already-built
mesh (only the new one is computed), and the cache key never depends on
which *other* labels were requested alongside it.
"""

from __future__ import annotations

from collections import OrderedDict

import numpy as np

_MAX_MESH_CACHE = 96
_mesh_cache: "OrderedDict[tuple, tuple]" = OrderedDict()
# {path: {"mtime_ns": int, "stats": {label: {z: (count, y1, y2, x1, x2)}},
#         "derived": rolled-up summary or None}} — see `label_summary`.
_summary_cache: dict[str, dict] = {}

# Meshing resolution: the longest side of a label's cropped bounding box is
# pooled down to at most this many cells before marching cubes runs. Bigger =
# more faithful + more triangles; this is a preview panel, not an export.
DEFAULT_TARGET_SIZE = 96
# Total triangles a single request may return, across all labels. "3D all" on
# a densely-annotated volume would otherwise ask for hundreds of surfaces at
# once and stall both the server and the WebGL context; past this we stop and
# report how many labels were left out (the panel says so).
DEFAULT_TRIANGLE_BUDGET = 1_200_000
# Per-slice statistics are gathered in parallel over chunks of z. Chunk size is
# picked by *bytes*, not slice count, so peak memory is bounded the same way on
# a 2k x 2k volume as on a 4.5k x 3.9k one.
_TARGET_CHUNK_BYTES = 96 << 20
_MAX_SCAN_WORKERS = 4
# Above this id, counting with a dense bincount array stops being the cheap
# option; fall back to sorting (np.unique). Instance ids are dense from 1, so
# in practice this never trips.
_MAX_BINCOUNT_ID = 1 << 22


def _scan_workers() -> int:
    """Threads to scan a volume with. numpy/scipy release the GIL inside
    ``bincount``/``find_objects``, so threads give a real speedup (measured
    ~4x on 4 workers) — but this shares a node with SAM inference and other
    requests, so it stays modest and respects the cgroup/SLURM CPU budget."""
    import os

    raw = os.environ.get("SLURM_CPUS_PER_TASK")
    if raw and raw.isdigit() and int(raw) > 0:
        usable = int(raw)
    else:
        try:
            usable = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            usable = os.cpu_count() or 1
    return max(1, min(_MAX_SCAN_WORKERS, usable))


def _slice_stats(sl: np.ndarray) -> dict[int, tuple[int, int, int, int, int]]:
    """``{label_id: (voxel_count, y1, y2, x1, x2)}`` for one 2D label slice
    (upper bounds exclusive).

    This per-slice granularity is what makes :func:`update_summary_for_slice`
    possible — the cached summary is a sum over slices, so re-writing one z
    means replacing one slice's contribution instead of rescanning the volume.
    It is also *faster* than the equivalent whole-chunk pass it replaced
    (measured 2.3s vs 9.3s single-threaded on a 137x2758x2514 volume):
    ``find_objects`` over a 3D chunk costs far more than over its 2D slices.
    """
    import scipy.ndimage as ndi

    if sl.size == 0:
        return {}
    top = int(sl.max())
    if top <= 0:
        return {}
    if top <= _MAX_BINCOUNT_ID:
        counts = np.bincount(sl.ravel(), minlength=top + 1)
        ids = np.nonzero(counts)[0]
    else:  # pragma: no cover - pathologically sparse ids
        ids, raw_counts = np.unique(sl, return_counts=True)
        counts = dict(zip(ids.tolist(), raw_counts.tolist()))
    try:
        objects = ndi.find_objects(sl)
    except (TypeError, RuntimeError):
        objects = ndi.find_objects(sl.astype(np.int32, copy=False))

    out: dict[int, tuple[int, int, int, int, int]] = {}
    for lid in ids.tolist():
        if lid <= 0 or lid - 1 >= len(objects):
            continue
        box = objects[lid - 1]
        if box is None:
            continue
        ys, xs = box
        out[int(lid)] = (
            int(counts[lid]),
            int(ys.start),
            int(ys.stop),
            int(xs.start),
            int(xs.stop),
        )
    return out


def _scan_stats(mm) -> dict[int, dict[int, tuple]]:
    """``{label_id: {z: (count, y1, y2, x1, x2)}}`` for a whole volume.

    ``mm`` is any lazy ``(Z, Y, X)`` view — a TIFF memmap or the HDF5 wrapper
    (see ``slice_io.open_label_volume_readonly``). The thread pool below stays
    worthwhile for both: HDF5 serialises the reads themselves behind h5py's
    global lock, but the per-plane statistics are numpy work that releases the
    GIL, which is the part being parallelised.
    """
    from concurrent.futures import ThreadPoolExecutor

    bytes_per_slice = max(1, int(np.prod(mm.shape[1:])) * mm.dtype.itemsize)
    chunk_z = max(1, min(int(mm.shape[0]), _TARGET_CHUNK_BYTES // bytes_per_slice))
    starts = list(range(0, mm.shape[0], chunk_z))

    def scan_chunk(z0: int):
        chunk = np.asarray(mm[z0 : z0 + chunk_z])
        return [(z0 + i, _slice_stats(chunk[i])) for i in range(chunk.shape[0])]

    workers = min(_scan_workers(), max(1, len(starts)))
    if workers == 1:
        results = [scan_chunk(z0) for z0 in starts]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(scan_chunk, starts))

    stats: dict[int, dict[int, tuple]] = {}
    for chunk_rows in results:
        for z, per_label in chunk_rows:
            for lid, entry in per_label.items():
                stats.setdefault(lid, {})[z] = entry
    return stats


def _derive_summary(stats: dict[int, dict[int, tuple]]) -> dict:
    """Roll per-slice statistics up into the shape callers consume."""
    labels = []
    bboxes = {}
    for lid in sorted(stats):
        per_z = stats[lid]
        if not per_z:
            continue
        zs = per_z.keys()
        z1, z2 = min(zs), max(zs)
        count = 0
        y1 = x1 = 1 << 62
        y2 = x2 = 0
        for c, ay1, ay2, ax1, ax2 in per_z.values():
            count += c
            y1 = min(y1, ay1)
            y2 = max(y2, ay2)
            x1 = min(x1, ax1)
            x2 = max(x2, ax2)
        labels.append({"id": lid, "voxel_count": count, "z_start": z1, "z_end": z2})
        bboxes[lid] = (z1, z2 + 1, y1, y2, x1, x2)
    return {"labels": labels, "bboxes": bboxes}


def label_summary(path) -> dict:
    """Per-instance-id voxel count, first/last z, **and** 3D bounding box
    across the whole *working* label volume. Backs the Labels panel's "All
    labels" scope (including jump-to-slice via ``z_start``) and every 3D mesh
    crop.

    Returns ``{"labels": [...], "bboxes": {id: (z1, z2, y1, y2, x1, x2)}}``
    with exclusive upper bounds, same convention as
    ``watershed.label_bbox_3d``. The bounding boxes ride along on this scan
    on purpose: computing them per label instead (``mask == lid`` over the
    whole volume, once per id) made "3D all" an O(labels x volume) job.

    **Caching.** The underlying per-slice statistics are cached per file and
    validated against its mtime. A slice edit does *not* invalidate them —
    :func:`update_summary_for_slice` folds the written slice in directly, so
    saving no longer costs a full rescan (which was 10-27s on the volumes
    here, on the request that follows every Save). Anything else that changes
    the file (whole-volume writes, another process, a restart) falls through
    to a fresh scan, so the cache can never silently drift.
    """
    from annotation.visualization.slice_io import open_label_volume_readonly

    path_str = str(path)
    if not path.exists():
        return {"labels": [], "bboxes": {}}
    mtime_ns = path.stat().st_mtime_ns
    entry = _summary_cache.get(path_str)
    if entry is not None and entry["mtime_ns"] == mtime_ns:
        if entry["derived"] is None:
            entry["derived"] = _derive_summary(entry["stats"])
        return entry["derived"]

    stats = _scan_stats(open_label_volume_readonly(path))
    derived = _derive_summary(stats)
    _summary_cache[path_str] = {"mtime_ns": mtime_ns, "stats": stats, "derived": derived}
    return derived


def update_summary_for_slice(path, z: int, new_slice: np.ndarray, *, mtime_ns_before: int) -> bool:
    """Fold one just-written z-slice into the cached summary, in place.

    Returns ``True`` when the cache was updated (the caller's next
    ``labels-summary`` is then O(labels), not O(volume)), ``False`` when
    there was nothing valid to update — in which case the next read simply
    rescans, which is the pre-existing behaviour.

    ``mtime_ns_before`` is the file's nanosecond mtime *before* the write: if the cache
    doesn't match it, someone else wrote in between and folding our slice in
    would produce a summary that never saw their change. Refusing the update
    there is what keeps this safe under more than one writer — the loser just
    pays for a rescan.
    """
    path_str = str(path)
    entry = _summary_cache.get(path_str)
    if entry is None or entry["mtime_ns"] != mtime_ns_before:
        return False
    try:
        mtime_ns_after = path.stat().st_mtime_ns
    except OSError:
        _summary_cache.pop(path_str, None)
        return False

    stats = entry["stats"]
    fresh = _slice_stats(np.asarray(new_slice))
    for lid in list(stats):
        per_z = stats[lid]
        if z in per_z and lid not in fresh:
            del per_z[z]
            if not per_z:
                del stats[lid]
    for lid, value in fresh.items():
        stats.setdefault(lid, {})[z] = value

    entry["mtime_ns"] = mtime_ns_after
    entry["derived"] = None
    return True


def forget_summary(path) -> None:
    """Drop the cached statistics for one file — for writers that change the
    volume wholesale (watershed / split / merge / tracking / reject), where a
    per-slice fold-in doesn't apply."""
    _summary_cache.pop(str(path), None)


def label_bboxes(path) -> dict:
    """``{label_id: (z1, z2, y1, y2, x1, x2)}`` for the whole working copy —
    one cached scan (see :func:`label_summary`), not one scan per label."""
    return label_summary(path)["bboxes"]


def _quantized_stride(extent: int, target_size: int) -> int:
    """Downsample factor for one axis of extent ``extent``, rounded up to a
    power of two. Quantizing keeps the mesh cache warm: two requests whose
    crops differ slightly still land on the same stride (and therefore the
    same cached geometry) instead of re-meshing everything."""
    raw = max(1.0, extent / float(max(target_size, 8)))
    stride = 1
    while stride < raw:
        stride *= 2
    return stride


def _axis_strides(extents: tuple[int, int, int], target_size: int) -> tuple[int, int, int]:
    """One stride **per axis**, not one for the whole box.

    EM labels are routinely far wider in x/y than they are deep in z (a mito
    spanning 400x400 px over 6 slices is normal). A single isotropic stride
    picked from the longest side would then pool those 6 slices down to one
    cell — the label comes back as a flat pancake, which is precisely the
    "stack of 2D slabs" look this module is here to eliminate.
    """
    return (
        _quantized_stride(extents[0], target_size),
        _quantized_stride(extents[1], target_size),
        _quantized_stride(extents[2], target_size),
    )


def _mean_pool(mask: np.ndarray, strides: tuple[int, int, int]) -> np.ndarray:
    """Pool a binary mask down per axis into an occupancy field in [0, 1].
    Mean (not max) on purpose: max-pooling snaps every partially filled block
    to a solid cube, which is the blocky "voxel Lego" look the mesh path
    exists to get rid of; the fractional occupancy is what lets marching cubes
    place the surface *between* grid cells."""
    field = mask.astype(np.float32)
    sz, sy, sx = strides
    if sz <= 1 and sy <= 1 and sx <= 1:
        return field
    pads = [(-field.shape[i]) % s for i, s in enumerate((sz, sy, sx))]
    if any(pads):
        field = np.pad(field, ((0, pads[0]), (0, pads[1]), (0, pads[2])))
    dz, dy, dx = field.shape
    return field.reshape(dz // sz, sz, dy // sy, sy, dx // sx, sx).mean(axis=(1, 3, 5))


def _label_mesh(
    mm, bbox, label_id: int, strides: tuple[int, int, int], padding: int
) -> tuple[np.ndarray, np.ndarray] | None:
    """Marching-cubes surface for one label, in whole-volume voxel (z, y, x)
    coordinates. Returns ``(vertices[N, 3] float32, faces[M, 3] uint32)`` or
    ``None`` when the label has no voxels in its crop."""
    import scipy.ndimage as ndi
    from skimage.measure import marching_cubes

    z1, z2, y1, y2, x1, x2 = bbox
    z1 = max(0, z1 - padding)
    y1 = max(0, y1 - padding)
    x1 = max(0, x1 - padding)
    z2 = min(mm.shape[0], z2 + padding)
    y2 = min(mm.shape[1], y2 + padding)
    x2 = min(mm.shape[2], x2 + padding)

    binmask = np.asarray(mm[z1:z2, y1:y2, x1:x2]) == label_id
    if not binmask.any():
        return None

    sz, sy, sx = strides
    field = _mean_pool(binmask, strides)
    # One empty cell of margin on every side so a label touching its crop
    # border still closes into a solid surface (marching cubes emits no faces
    # on the array boundary — without this, big labels render as open shells).
    field = np.pad(field, 1)
    # Light blur: rounds the staircase left by the voxel grid without moving
    # the iso-surface off the mask (sigma < 1 cell).
    field = ndi.gaussian_filter(field, sigma=0.7, mode="constant")
    peak = float(field.max())
    if peak <= 0.0:
        return None
    # A label thinner than the pooling stride can blur below 0.5 everywhere;
    # meeting it half way still yields its real shape instead of nothing.
    level = 0.5 if peak > 0.5 else peak * 0.5
    if level <= 0.0:
        return None

    verts, faces, _normals, _values = marching_cubes(
        field, level=level, spacing=(float(sz), float(sy), float(sx))
    )
    # Undo the 1-cell pad, re-center pooled cells on the voxels they cover,
    # then lift into whole-volume voxel coordinates.
    verts = verts.astype(np.float32, copy=False)
    verts += np.asarray(
        [z1 - sz + (sz - 1) / 2.0, y1 - sy + (sy - 1) / 2.0, x1 - sx + (sx - 1) / 2.0],
        dtype=np.float32,
    )
    return verts, faces.astype(np.uint32, copy=False)


def labels_3d_mesh(
    path,
    label_ids: list[int],
    *,
    target_size: int = DEFAULT_TARGET_SIZE,
    padding: int = 2,
    triangle_budget: int = DEFAULT_TRIANGLE_BUDGET,
) -> dict:
    """Per-label iso-surface meshes for the requested ids.

    Returns::

        {
          "origin": (z, y, x),   # min corner of all emitted geometry, voxels
          "size":   (dz, dy, dx),
          "meshes": [{"id": int, "vertices": float32[N,3], "faces": uint32[M,3]}],
          "truncated": int,      # labels skipped because of triangle_budget
        }

    Vertices are whole-volume voxel coordinates in (z, y, x) order — physical
    anisotropy (``Volume.voxel_size_*``) is applied by the caller/renderer, so
    the cached geometry stays independent of it.
    """
    from annotation.visualization.slice_io import open_label_volume_readonly

    empty = {"origin": (0.0, 0.0, 0.0), "size": (0.0, 0.0, 0.0), "meshes": [], "truncated": 0}
    if not label_ids or not path.exists():
        return empty

    path_str = str(path)
    mtime_ns = path.stat().st_mtime_ns
    bboxes = label_bboxes(path)
    # Background is never a surface, even if a malformed/stale summary were
    # ever to contain it. Keep this guard at the mesh boundary as well as in
    # the summary scanner.
    wanted = [
        lid for lid in dict.fromkeys(int(v) for v in label_ids)
        if lid > 0 and lid in bboxes
    ]
    if not wanted:
        return empty

    mm = open_label_volume_readonly(path)

    meshes = []
    triangles = 0
    truncated = 0
    for lid in wanted:
        if triangles >= triangle_budget:
            truncated += 1
            continue
        bbox = bboxes[lid]
        extents = (bbox[1] - bbox[0], bbox[3] - bbox[2], bbox[5] - bbox[4])
        # Resolution belongs to this label, not to how many unrelated labels
        # happen to be pinned beside it. The request triangle budget remains
        # the aggregate resource bound.
        strides = _axis_strides(extents, target_size)
        key = (path_str, mtime_ns, lid, strides, padding)
        cached = _mesh_cache.get(key)
        if cached is not None:
            _mesh_cache.move_to_end(key)
        else:
            cached = _label_mesh(mm, bboxes[lid], lid, strides, padding)
            _mesh_cache[key] = cached
            _mesh_cache.move_to_end(key)
            while len(_mesh_cache) > _MAX_MESH_CACHE:
                _mesh_cache.popitem(last=False)
        if cached is None:
            continue
        verts, faces = cached
        triangles += len(faces)
        meshes.append({"id": lid, "vertices": verts, "faces": faces})

    if not meshes:
        return empty

    mins = np.min([m["vertices"].min(axis=0) for m in meshes], axis=0)
    maxs = np.max([m["vertices"].max(axis=0) for m in meshes], axis=0)
    return {
        "origin": tuple(float(v) for v in mins),
        "size": tuple(float(v) for v in (maxs - mins)),
        "meshes": meshes,
        "truncated": truncated,
    }


def _block_max_pool(mask: np.ndarray, stride: int) -> np.ndarray:
    if stride <= 1:
        return mask.astype(np.uint8)
    pz = (-mask.shape[0]) % stride
    py = (-mask.shape[1]) % stride
    px = (-mask.shape[2]) % stride
    padded = np.pad(mask, ((0, pz), (0, py), (0, px)))
    dz, dy, dx = padded.shape
    reshaped = padded.reshape(
        dz // stride, stride, dy // stride, stride, dx // stride, stride
    )
    return reshaped.max(axis=(1, 3, 5)).astype(np.uint8)


def labels_3d_preview(path, label_ids: list[int], target_size: int = 72, padding: int = 4) -> dict:
    """Downsampled per-label binary voxel grids, cropped to the union bbox of
    ``label_ids``. **Legacy** — the 3D panel renders :func:`labels_3d_mesh`
    now; this still backs the older ``labels-3d`` grid endpoint (kept so an
    old client/bookmark doesn't 404, and as a dependency-free fallback if
    scikit-image is unavailable).

    Returns ``{"shape": (dz, dy, dx), "grids": {label_id: np.ndarray[uint8]}}``
    — an empty result if the file doesn't exist or none of ``label_ids`` are
    present.
    """
    from annotation.visualization.slice_io import open_label_volume_readonly

    if not label_ids or not path.exists():
        return {"shape": (0, 0, 0), "grids": {}}

    bboxes = label_bboxes(path)
    boxes = [bboxes[lid] for lid in dict.fromkeys(int(v) for v in label_ids) if lid in bboxes]
    if not boxes:
        return {"shape": (0, 0, 0), "grids": {}}

    mm = open_label_volume_readonly(path)
    z1 = max(0, min(b[0] for b in boxes) - padding)
    z2 = min(mm.shape[0], max(b[1] for b in boxes) + padding)
    y1 = max(0, min(b[2] for b in boxes) - padding)
    y2 = min(mm.shape[1], max(b[3] for b in boxes) + padding)
    x1 = max(0, min(b[4] for b in boxes) - padding)
    x2 = min(mm.shape[2], max(b[5] for b in boxes) + padding)

    region = np.asarray(mm[z1:z2, y1:y2, x1:x2])
    stride = max(1, int(np.ceil(max(region.shape) / target_size)))

    grids: dict[int, np.ndarray] = {}
    for lid in label_ids:
        binmask = region == lid
        if not binmask.any():
            continue
        grids[lid] = _block_max_pool(binmask, stride)

    shape = next(iter(grids.values())).shape if grids else (0, 0, 0)
    return {"shape": shape, "grids": grids}
