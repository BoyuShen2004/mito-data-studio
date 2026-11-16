"""Export the stitched segmentation as a Neuroglancer precomputed layer.

``stitch_export`` writes every tile's labels into one uint64 segmentation in
which each voxel holds its stitched object's ID (``objects.csv``; the smallest
global ID of the object), so the lab can view the result in Neuroglancer and
spot-check seams. With ``ids="label"`` each voxel holds its own global ID
instead, which shows the pieces before merging.

The layer uses the dataset's voxel coordinates: its ``voxel_offset`` is the
lowest corner of the tiled volume, so positions match the tile names and the
bounding boxes in ``measurements.csv`` and the delivery. Positions with no
tile (missing grid positions, gaps) are background and store no chunks.

Written with tensorstore (already required by ``measure_volume``): scale 0
only, unsharded, ``compressed_segmentation``. Arrays are (Z, Y, X) here and
transposed to precomputed's (x, y, z) at the write. Tiles are streamed in
blocks, never loaded whole. The design is in ``docs/engineering/stitching.md``
("Neuroglancer export").
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from annotation.measurements import MeasurementError, validate_voxel_size
from annotation.stitching import (
    StitchingError,
    Tile,
    _check_label_dtype,
    build_inventory,
    code_revision,
    load_region_voxels,
    load_tile_ids,
    load_tiles_csv,
    open_tile,
    read_csv,
    require_stitchable,
)

DEFAULT_CHUNK_XYZ = (128, 128, 64)
EXPORT_RUN = "export_run.json"
ID_MODES = ("object", "label")
# Output bytes (uint64) held per block while a tile is streamed.
BLOCK_BYTES = 512 * 1024 * 1024
# Above this largest local ID a tile is mapped through np.unique instead of a lookup table.
MAX_LOOKUP_ID = 1 << 26


def _tensorstore():
    try:
        import tensorstore
    except ImportError:  # pragma: no cover - environment problem
        raise StitchingError("stitch_export needs tensorstore (requirements/release.in)") from None
    return tensorstore


def volume_box(tiles: list[Tile]) -> tuple[int, ...]:
    """Half-open (z0, z1, y0, y1, x0, x1) bounding box of all tiles."""
    return tuple(v for a in range(3) for v in (min(t.lo(a) for t in tiles), max(t.hi(a) for t in tiles)))


def precomputed_info(box, voxel_zyx, chunk_xyz) -> dict:
    """The layer's ``info``: one uint64 segmentation scale in (x, y, z) order."""
    size_zyx = [box[2 * a + 1] - box[2 * a] for a in range(3)]
    resolution = [float(v) for v in voxel_zyx[::-1]]
    return {
        "@type": "neuroglancer_multiscale_volume", "type": "segmentation", "data_type": "uint64",
        "num_channels": 1,
        "scales": [{
            "key": "_".join(f"{v:g}" for v in resolution), "size": size_zyx[::-1],
            "voxel_offset": [box[4], box[2], box[0]], "resolution": resolution,
            "chunk_sizes": [list(chunk_xyz)], "encoding": "compressed_segmentation",
            "compressed_segmentation_block_size": [8, 8, 8],
        }],
    }


def create_store(out: Path, info: dict):
    ts = _tensorstore()
    scale = info["scales"][0]
    try:
        return ts.open({
            "driver": "neuroglancer_precomputed",
            "kvstore": {"driver": "file", "path": str(out) + "/"},
            "multiscale_metadata": {"type": "segmentation", "data_type": "uint64", "num_channels": 1},
            "scale_metadata": {
                "key": scale["key"], "size": scale["size"], "voxel_offset": scale["voxel_offset"],
                "resolution": scale["resolution"], "chunk_size": scale["chunk_sizes"][0],
                "encoding": scale["encoding"],
                "compressed_segmentation_block_size": scale["compressed_segmentation_block_size"],
            },
            "create": True,
        }).result()
    except Exception as exc:  # tensorstore raises ValueError and its own errors
        raise StitchingError(f"cannot create {out}: {exc}") from None


def tile_blocks(tile: Tile, box, chunk_zyx, block_bytes: int = BLOCK_BYTES):
    """Local ``(z0, z1, y0, y1)`` blocks covering ``tile``, cut on the layer's
    chunk grid in Z and Y where the tile allows, so a block rewrites as few
    partly written chunks as possible. Each holds at most ``block_bytes`` of
    uint64 output (at least one chunk row)."""
    nz, ny, nx = tile.shape

    def cuts(axis: int, n: int, step: int) -> list[int]:
        grid = chunk_zyx[axis]
        step = max(grid, step // grid * grid)
        misaligned = (tile.lo(axis) - box[2 * axis]) % grid
        edges = [0]
        if misaligned:      # first cut on the next chunk boundary
            edges.append(min(n, grid - misaligned))
        while edges[-1] < n:
            edges.append(min(n, edges[-1] + step))
        return edges

    z_edges = cuts(0, nz, chunk_zyx[0])
    rows = max(1, block_bytes // (8 * chunk_zyx[0] * max(1, nx)))
    y_edges = cuts(1, ny, rows)
    for z0, z1 in zip(z_edges, z_edges[1:]):
        for y0, y1 in zip(y_edges, y_edges[1:]):
            yield z0, z1, y0, y1


def map_block(block: np.ndarray, mapping: dict[int, int], lookup: np.ndarray | None, where: str) -> np.ndarray:
    """``block`` of local IDs as uint64 output IDs. A nonzero local ID missing
    from ``mapping`` is an error: the tile no longer matches id_map.csv."""
    if lookup is not None:
        if block.size and int(block.max()) >= lookup.size:
            raise StitchingError(f"{where}: label {int(block.max())} is not in id_map.csv; rerun stitch_ids")
        return lookup[block]
    ids, inverse = np.unique(block, return_inverse=True)
    unknown = [lid for lid in ids.tolist() if lid and lid not in mapping]
    if unknown:
        raise StitchingError(f"{where}: label {unknown[0]} is not in id_map.csv; rerun stitch_ids")
    values = np.array([mapping.get(lid, 0) if lid else 0 for lid in ids.tolist()], np.uint64)
    return values[inverse.reshape(block.shape)]


@dataclass
class ExportResult:
    out: Path
    info: dict
    counts: dict = field(default_factory=dict)
    run: dict = field(default_factory=dict)


def run_export(work, out, voxel_size_zyx, *, ids: str = "object", region_only: bool = False,
               chunk_xyz=DEFAULT_CHUNK_XYZ, overwrite: bool = False, command: str = "",
               block_bytes: int = BLOCK_BYTES, log=lambda message: None) -> ExportResult:
    work, out = Path(work), Path(out).expanduser().resolve()
    try:
        voxel = validate_voxel_size(voxel_size_zyx)
    except MeasurementError as exc:
        raise StitchingError(str(exc)) from None
    if ids not in ID_MODES:
        raise StitchingError(f"--ids must be one of {', '.join(ID_MODES)}")
    chunk_xyz = tuple(int(v) for v in chunk_xyz)
    if len(chunk_xyz) != 3 or any(v < 1 for v in chunk_xyz):
        raise StitchingError("--chunk-size-xyz must be three positive integers")
    chunk_zyx = chunk_xyz[::-1]

    inventory = build_inventory(load_tiles_csv(work))
    require_stitchable(inventory)
    tiles = inventory.tiles
    tile_ids = load_tile_ids(work, tiles)      # refuses tiles changed since stitch_ids
    id_rows = read_csv(work / "id_map.csv")
    object_of = {int(r["global_id"]): int(r["object_id"]) for r in read_csv(work / "objects.csv")}
    if set(object_of) != {int(r["global_id"]) for r in id_rows}:
        raise StitchingError("objects.csv does not cover exactly the ids in id_map.csv; rerun stitch_objects")
    region_voxels = load_region_voxels(work)
    if region_only and region_voxels is None:
        raise StitchingError("--region-only needs region data; run stitch_ids --regions and the later phases")
    kept = None
    if region_only:     # objects reaching into the region, as stitch_measure --region-only lists them
        kept = {object_of[g] for g, n in region_voxels.items() if n > 0}

    if out.exists() and any(out.iterdir()):
        if not overwrite:
            raise StitchingError(f"{out} is not empty; pass --overwrite to replace the layer")
        if not (out / "info").is_file():
            raise StitchingError(f"{out} is not empty and holds no precomputed 'info'; refusing to overwrite it")
        shutil.rmtree(out)

    mappings: dict[str, dict[int, int]] = {t.name: {} for t in tiles}
    for r in id_rows:
        g = int(r["global_id"])
        if kept is not None and object_of[g] not in kept:
            continue
        mappings[r["tile"]][int(r["local_id"])] = object_of[g] if ids == "object" else g

    box = volume_box(tiles)
    info = precomputed_info(box, voxel, chunk_xyz)
    # Always say which voxel size is written: recorded resolutions have been wrong before (p7 was off by 4x).
    log("voxel size written: {:g} x {:g} x {:g} nm (Z, Y, X), from --voxel-size-zyx".format(*voxel))
    log(f"layer {out}: size {info['scales'][0]['size']} voxels, voxel_offset {info['scales'][0]['voxel_offset']} "
        f"(x, y, z), chunks {list(chunk_xyz)}, ids = {ids}" + (", region only" if region_only else ""))
    out.mkdir(parents=True, exist_ok=True)
    store = create_store(out, info)

    started = time.monotonic()
    written = set()
    voxels = 0
    for index, tile in enumerate(tiles, 1):
        t0 = time.monotonic()
        mapping = mappings[tile.name]
        max_local = tile_ids[tile.name]["max_local_id"]
        lookup = None
        if max_local <= MAX_LOOKUP_ID:
            lookup = np.zeros(max_local + 1, np.uint64)
            for local, value in mapping.items():
                lookup[local] = value
        with open_tile(tile.path) as volume:
            if tuple(volume.shape) != tile.shape:
                raise StitchingError(f"{tile.path}: shape {tuple(volume.shape)} does not match tiles.csv")
            _check_label_dtype(volume.dtype, tile.name)
            for z0, z1, y0, y1 in tile_blocks(tile, box, chunk_zyx, block_bytes):
                block = np.asarray(volume[z0:z1, y0:y1, :])
                if not block.any():
                    continue        # background stores no chunks
                mapped = map_block(block, mapping, lookup, tile.name)
                voxels += int(np.count_nonzero(mapped))
                written.update(np.unique(mapped).tolist())
                gz, gy, gx = tile.lo(0) + z0, tile.lo(1) + y0, tile.lo(2)
                store[gx:gx + tile.shape[2], gy:gy + (y1 - y0), gz:gz + (z1 - z0), 0].write(
                    mapped.transpose(2, 1, 0)).result()
        log(f"tile {index}/{len(tiles)} {tile.name}: {len(mapping)} label(s) in {time.monotonic() - t0:.1f}s")
    written.discard(0)

    counts = {"tiles": len(tiles), "segments": len(written), "labelled_voxels": voxels,
              "seconds": round(time.monotonic() - started)}
    run = {
        "command": command, "layer": str(out), "ids": ids, "region_only": region_only,
        "voxel_size_zyx_nm": list(voxel), "size_xyz": info["scales"][0]["size"],
        "voxel_offset_xyz": info["scales"][0]["voxel_offset"], "chunk_size_xyz": list(chunk_xyz),
        "scale_key": info["scales"][0]["key"], "encoding": info["scales"][0]["encoding"],
        "counts": counts, "code_revision": code_revision(),
        "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (work / EXPORT_RUN).write_text(json.dumps(run, indent=2) + "\n")
    return ExportResult(out, info, counts, run)
