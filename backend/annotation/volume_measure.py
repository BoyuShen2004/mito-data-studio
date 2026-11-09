"""Measure mitochondria in one large, already-stitched Neuroglancer volume.

``measure_volume`` is the counterpart of stitching phases 6-7 for a volume
that was stitched and seam-proofread upstream (first use: p7). It reads a
Neuroglancer precomputed segmentation (scale 0, unsharded) with tensorstore
and writes the same delivery files as ``stitch_deliver``, so p7 and p10 line
up column for column.

- **Pass 1** visits only the chunk files that exist (listed from the scale
  directory; the volume is never read whole). Per label it merges voxel count,
  bounding box and region voxel count over chunks.
- **Pass 2** reads each listed label's padded crop and measures it with the
  per-volume code (``annotation.measurements``): same crop rule, dust
  threshold, TEASAR parameters, voxel size passed once in (Z, Y, X) nm.
- The delivery tables, summary and README sections come from
  ``annotation.stitching_deliver``.

Arrays are (Z, Y, X); tensorstore's precomputed order is (x, y, z, channel)
and is transposed at the read boundary. Coordinates are the volume's own voxel
coordinates (Neuroglancer's), including any ``voxel_offset``. The design is in
``docs/engineering/stitching.md`` ("Measuring an upstream-stitched volume").
"""

from __future__ import annotations

import importlib.metadata
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import scipy.ndimage as ndi

from annotation.measurements import (
    CROP_PAD,
    SKIPPED_CROP_LIMIT,
    MeasurementError,
    skeleton_length_nm,
    validate_voxel_size,
)
from annotation.stitching import REGION_VOXELS, StitchingError, _listing, code_revision, write_csv
from annotation.stitching_deliver import (
    DELIVERY_FILES,
    EXCLUDED_DOCS,
    EXCLUDED_FIELDS,
    MITO_FIELDS,
    SUMMARY_FIELDS,
    SUMMARY_METRICS,
    _column_docs,
    mito_row,
    read_exclusions,
    readme_files_lines,
    readme_length_lines,
    readme_tables_lines,
    readme_units_lines,
    summarise,
)
from annotation.stitching_measure import (
    BBOX_FIELDS,
    CENTER_FIELDS,
    DEFAULT_MAX_CROP_VOXELS,
    MEASURE_RUN,
    box_voxels,
    mask_moments,
    padded_window,
)

# Decoded-chunk cache shared by every read (tensorstore cache pool). Pass 2
# visits labels in chunk order, so neighbouring crops reuse decoded chunks.
CACHE_BYTES = 1 << 30
LABEL_STATS_FIELDS = ("label_id", "voxel_count", REGION_VOXELS, *BBOX_FIELDS)
_CHUNK_RE = re.compile(r"^(\d+)-(\d+)_(\d+)-(\d+)_(\d+)-(\d+)$")
_LABEL_TYPES = ("uint8", "uint16", "uint32", "uint64")


class VolumeMeasureError(StitchingError):
    """Raised when the volume cannot be measured as asked."""


def _tensorstore():
    try:
        import tensorstore
    except ImportError:  # pragma: no cover - environment problem
        raise VolumeMeasureError("measure_volume needs tensorstore (requirements/release.in)") from None
    return tensorstore


def tensorstore_version() -> str:
    try:
        return importlib.metadata.version("tensorstore")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        return "unknown"


def _zyx(values) -> tuple:
    """A precomputed (x, y, z) triple in (Z, Y, X) order."""
    x, y, z = values
    return (z, y, x)


# --- the volume --------------------------------------------------------------

@dataclass(frozen=True)
class PrecomputedVolume:
    path: Path
    key: str                         # scale 0 directory name
    size: tuple[int, int, int]       # (Z, Y, X) voxels
    offset: tuple[int, int, int]     # (Z, Y, X) voxel_offset
    chunk: tuple[int, int, int]      # (Z, Y, X) chunk size
    resolution: tuple[float, float, float]   # (Z, Y, X) nm, as the metadata says
    encoding: str
    data_type: str

    @property
    def box(self) -> tuple[int, ...]:
        """Half-open (z0, z1, y0, y1, x0, x1) in the volume's voxel coordinates."""
        return tuple(v for a in range(3) for v in (self.offset[a], self.offset[a] + self.size[a]))

    def describe(self) -> dict:
        return {"path": str(self.path), "scale_key": self.key, "size_zyx": list(self.size),
                "voxel_offset_zyx": list(self.offset), "chunk_size_zyx": list(self.chunk),
                "metadata_resolution_zyx_nm": list(self.resolution), "encoding": self.encoding,
                "data_type": self.data_type}


def read_info(path, *, what: str) -> PrecomputedVolume:
    """Scale 0 of the precomputed volume at ``path`` (a local directory)."""
    path = Path(path).expanduser().resolve()
    info_path = path / "info"
    if not info_path.is_file():
        raise VolumeMeasureError(f"{what}: {info_path} not found; expected a Neuroglancer precomputed directory")
    try:
        info = json.loads(info_path.read_text())
        scale = info["scales"][0]
        num_channels, data_type = int(info.get("num_channels", 1)), str(info["data_type"])
        key, encoding = str(scale["key"]), str(scale.get("encoding", "raw"))
        size, offset = _zyx(scale["size"]), _zyx(scale.get("voxel_offset", [0, 0, 0]))
        resolution = _zyx(float(v) for v in scale["resolution"])
        chunk_sizes = scale["chunk_sizes"]
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise VolumeMeasureError(f"{what}: cannot read {info_path}: {exc}") from None
    if scale.get("sharding"):
        raise VolumeMeasureError(f"{what}: scale 0 is sharded; only unsharded volumes are supported "
                                 "(chunk files are listed to visit only existing data)")
    if num_channels != 1:
        raise VolumeMeasureError(f"{what}: {num_channels} channels; expected 1")
    if len(chunk_sizes) != 1:
        raise VolumeMeasureError(f"{what}: scale 0 lists {len(chunk_sizes)} chunk sizes; expected 1")
    return PrecomputedVolume(path, key, tuple(int(v) for v in size), tuple(int(v) for v in offset),
                             tuple(int(v) for v in _zyx(chunk_sizes[0])), tuple(resolution), encoding, data_type)


def list_chunks(volume: PrecomputedVolume) -> list[tuple[int, ...]]:
    """Boxes ``(z0, z1, y0, y1, x0, x1)`` of the chunk files that exist, in
    (z, y, x) order. Missing chunks are background (Neuroglancer omits
    all-zero chunks). Any other file in the scale directory is refused, so a
    renamed or compressed chunk is never silently skipped."""
    directory = volume.path / volume.key
    if not directory.is_dir():
        raise VolumeMeasureError(f"{directory} not found (scale 0 key {volume.key!r})")
    boxes, problems = [], []
    lo, hi = volume.box[0::2], volume.box[1::2]
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            match = _CHUNK_RE.match(entry.name)
            if not match or not entry.is_file():
                problems.append(f"{entry.name}: not a chunk file name x0-x1_y0-y1_z0-z1")
                continue
            x0, x1, y0, y1, z0, z1 = map(int, match.groups())
            box = (z0, z1, y0, y1, x0, x1)
            for a in range(3):
                a0, a1 = box[2 * a], box[2 * a + 1]
                if (a0 - lo[a]) % volume.chunk[a] or a0 < lo[a] or a1 != min(a0 + volume.chunk[a], hi[a]):
                    problems.append(f"{entry.name}: not on the {volume.chunk[::-1]} (x, y, z) chunk grid of the "
                                    "volume")
                    break
            else:
                boxes.append(box)
    if problems:
        raise VolumeMeasureError(_listing(f"{directory}: unexpected files", sorted(problems)))
    return sorted(boxes)


def open_store(volume: PrecomputedVolume, cache_bytes: int = CACHE_BYTES):
    ts = _tensorstore()
    try:
        return ts.open({
            "driver": "neuroglancer_precomputed",
            "kvstore": {"driver": "file", "path": str(volume.path) + "/"},
            "scale_index": 0,
            "recheck_cached_data": "open",
            "context": {"cache_pool": {"total_bytes_limit": cache_bytes}},
        }, read=True).result()
    except Exception as exc:  # tensorstore raises ValueError and its own errors
        raise VolumeMeasureError(f"cannot open {volume.path}: {exc}") from None


def start_read(store, box):
    """Start reading ``box`` (volume coordinates); ``finish_read`` returns it."""
    z0, z1, y0, y1, x0, x1 = box
    return store[x0:x1, y0:y1, z0:z1, 0].read()


def finish_read(future) -> np.ndarray:
    """The (Z, Y, X) array of a started read (missing chunks are 0)."""
    return np.ascontiguousarray(np.asarray(future.result()).transpose(2, 1, 0))


def read_box(store, box) -> np.ndarray:
    """The (Z, Y, X) array of ``box`` (volume coordinates; missing chunks are 0)."""
    return finish_read(start_read(store, box))


# --- pass 1 ------------------------------------------------------------------

def chunk_label_stats(labels: np.ndarray, region: np.ndarray | None):
    """``[(label, voxels, region voxels, local box)]`` for the nonzero labels
    of one (Z, Y, X) chunk; boxes are half-open, relative to the chunk."""
    ids, inverse, counts = np.unique(labels, return_inverse=True, return_counts=True)
    shift = 1 if ids[0] != 0 else 0      # compact value of ids[i] is i + shift; 0 stays background
    compact = (inverse.reshape(labels.shape) + shift).astype(np.int32 if ids.size < 2**31 - 1 else np.int64)
    objects = ndi.find_objects(compact)
    in_region = (np.bincount(compact[region != 0], minlength=ids.size + shift) if region is not None
                 else np.zeros(ids.size + shift, np.int64))
    out = []
    for i, lid in enumerate(ids.tolist()):
        if lid == 0:
            continue
        box = objects[i + shift - 1]
        out.append((int(lid), int(counts[i]), int(in_region[i + shift]),
                    tuple(v for s in box for v in (s.start, s.stop))))
    return out


@dataclass
class LabelStats:
    voxel_count: int
    region_voxels: int
    box: list[int]          # (z0, z1, y0, y1, x0, x1), volume coordinates

    def merge(self, count: int, region: int, box) -> None:
        self.voxel_count += count
        self.region_voxels += region
        for a in range(3):
            self.box[2 * a] = min(self.box[2 * a], box[2 * a])
            self.box[2 * a + 1] = max(self.box[2 * a + 1], box[2 * a + 1])


def scan_labels(store, chunks, region_store=None, *, log=lambda message: None) -> dict[int, LabelStats]:
    """Pass 1: per-label stats merged over the existing chunks."""
    stats: dict[int, LabelStats] = {}
    started = time.monotonic()
    step = max(1, len(chunks) // 20)

    def start(box):
        return start_read(store, box), (start_read(region_store, box) if region_store is not None else None)

    pending = start(chunks[0]) if chunks else None
    for index, box in enumerate(chunks, 1):
        labels_read, region_read = pending
        if index < len(chunks):     # decode the next chunk while this one is counted
            pending = start(chunks[index])
        labels = finish_read(labels_read)
        region = finish_read(region_read) if region_read is not None else None
        origin = box[0::2]
        for lid, count, inside, local in chunk_label_stats(labels, region):
            shifted = [local[i] + origin[i // 2] for i in range(6)]
            entry = stats.get(lid)
            if entry is None:
                stats[lid] = LabelStats(count, inside, shifted)
            else:
                entry.merge(count, inside, shifted)
        if index % step == 0 or index == len(chunks):
            elapsed = time.monotonic() - started
            log(f"pass 1: chunk {index}/{len(chunks)}, {len(stats)} label(s) so far, {elapsed:.0f}s"
                + (f", about {elapsed / index * (len(chunks) - index):.0f}s left" if index < len(chunks) else ""))
    return stats


# --- pass 2 ------------------------------------------------------------------

@dataclass
class LabelMeasurement:
    label_id: int
    voxel_count: int
    region_voxels: int | None
    box: tuple[int, ...]
    touches_edge: bool
    coord_sums: tuple[int, int, int] = (0, 0, 0)
    skeleton_length_um: float | None = None
    skipped_reason: str = ""

    def center_nm(self, voxel) -> list[str]:
        return [repr(self.coord_sums[a] / self.voxel_count * voxel[a]) for a in range(3)]


def touches_outer_face(box, volume_box) -> bool:
    """The per-volume edge rule: a box on any of the volume's six outer faces."""
    return any(box[i] == volume_box[i] for i in range(6))


def measure_label(store, lid: int, stats: LabelStats, volume_box, voxel, max_crop_voxels: int,
                  *, has_region: bool) -> LabelMeasurement:
    box = tuple(stats.box)
    m = LabelMeasurement(lid, stats.voxel_count, stats.region_voxels if has_region else None, box,
                         touches_outer_face(box, volume_box))
    window = padded_window(box, volume_box)
    if box_voxels(window) > max_crop_voxels:
        # No skeleton, but the centroid is still exact: read the window in Z slabs.
        m.skipped_reason = SKIPPED_CROP_LIMIT
        plane = box_voxels((0, 1, *window[2:]))
        step = max(1, max_crop_voxels // max(1, plane))
        count, sums = 0, [0, 0, 0]
        for z in range(window[0], window[1], step):
            slab = (z, min(z + step, window[1]), *window[2:])
            c, s = mask_moments(read_box(store, slab) == lid, slab)
            count += c
            sums = [a + b for a, b in zip(sums, s)]
        moments = (count, tuple(sums))
    else:
        mask = read_box(store, window) == lid
        moments = mask_moments(mask, window)
    if moments[0] != stats.voxel_count:
        raise VolumeMeasureError(f"label {lid}: {moments[0]} voxels in its crop but {stats.voxel_count} in pass 1; "
                                 "did the volume change during the run?")
    m.coord_sums = moments[1]
    if not m.skipped_reason:
        m.skeleton_length_um = skeleton_length_nm(mask, voxel) / 1000.0
    return m


# --- delivery ----------------------------------------------------------------

def delivery_row(m: LabelMeasurement, name: str, voxel) -> tuple[dict, dict[str, bool]]:
    """One ``mitochondria.csv`` row through ``stitch_deliver``'s ``mito_row``:
    one volume is one "tile", with no unproofread tiles or unresolved seams."""
    voxel_nm3 = voxel[0] * voxel[1] * voxel[2]
    region = m.region_voxels
    measured = {
        "object_id": m.label_id, "volume_um3": repr(m.voxel_count * voxel_nm3 / 1e9),
        "skeleton_length_um": "" if m.skeleton_length_um is None else repr(m.skeleton_length_um),
        "skipped_reason": m.skipped_reason, "touches_edge": "true" if m.touches_edge else "false",
        "crosses_region_boundary": "true" if region is not None and 0 < region < m.voxel_count else "false",
        "unproofread_tiles": 0, "unresolved_contacts": 0, "tiles": name,
        REGION_VOXELS: region or 0, "frac_in_region": "" if region is None else repr(region / m.voxel_count),
        **dict(zip(BBOX_FIELDS, m.box)), **dict(zip(CENTER_FIELDS, m.center_nm(voxel))),
    }
    row, flags = mito_row(measured, voxel_nm3)
    if region is None:      # no region mask: the region columns are empty, not zero
        row["volume_in_region_um3"] = ""
    return row, flags


class _AnyLabel(dict):
    """Accepts every integer, so ``read_exclusions`` can check a file's syntax
    before pass 1 (unknown labels are checked after it)."""

    def __contains__(self, key) -> bool:
        return True

    def __missing__(self, key):
        return key


def volume_column_docs(voxel, name: str, *, has_region: bool) -> dict[str, str]:
    docs = _column_docs(voxel)
    no_region = "Empty: no region mask was given."
    docs.update({
        "mito_id": "Identifier of the mitochondrion: its label ID in the input volume, so it can be found there "
                   "(for example in Neuroglancer). IDs are not consecutive.",
        "volume_um3": "Volume of the whole mitochondrion in µm³ (voxel count × voxel volume)"
                      + (", including any part outside the region." if has_region else "."),
        "volume_in_region_um3": docs["volume_in_region_um3"] if has_region else no_region,
        "frac_in_region": docs["frac_in_region"] if has_region else no_region,
        "n_tiles": "Always 1: the input is one already-stitched volume, not tiles (see *Constant columns*).",
        "tiles": f"The name of that volume, `{name}`.",
        "cut_off_at_data_edge": "`true` when the mitochondrion touches one of the six outer faces of the volume, "
                                "so part of it may be missing and its volume and length are lower bounds.",
        "crosses_region_boundary": ("`true` when it lies partly inside and partly outside the region." if has_region
                                    else "Always `false`: no region mask was given."),
        "touches_unproofread_tile": "Always `false` (see *Constant columns*).",
        "core": "`true` when the measurement can be trusted as complete: not cut off at the data edge"
                + (" and not crossing the region boundary." if has_region else ".")
                + " Use core objects for statistics that need whole mitochondria.",
        "not_core_reasons": "Why `core` is `false`, separated by `;`: `cut_off_at_data_edge`"
                            + (", `crosses_region_boundary`" if has_region else "")
                            + ". (`touches_unproofread_tile` and `unresolved_contacts` never occur here; see "
                            "*Constant columns*.) Empty for core objects.",
    })
    return docs


def build_volume_readme(ctx: dict) -> str:
    voxel, name = ctx["voxel"], ctx["name"]
    labels: PrecomputedVolume = ctx["labels"]
    region: PrecomputedVolume | None = ctx["region"]
    s_all, s_core = ctx["summary_all"], ctx["summary_core"]
    vz, vy, vx = (f"{v:g}" for v in voxel)
    mz, my, mx = (f"{v:g}" for v in labels.resolution)
    sz, sy, sx = labels.size
    if ctx["region_only"]:
        listed = "One row per mitochondrion that reaches into the region (exclusions removed)."
        scope = [
            "## Scope: region only",
            "",
            f"Only mitochondria that reach into the region (the mask `{region.path}`, nonzero = inside) are "
            "listed. A mitochondrion that is partly outside is still listed whole (its volume and length include "
            "the outside part), and is flagged `crosses_region_boundary`; `volume_in_region_um3` and "
            "`frac_in_region` give the inside share.",
        ]
    elif region is not None:
        listed = "One row per labelled mitochondrion in the volume (exclusions removed)."
        scope = [
            "## Scope: the whole volume",
            "",
            f"Every label in the volume is listed. The region mask `{region.path}` (nonzero = inside) only fills "
            "`volume_in_region_um3`, `frac_in_region` and `crosses_region_boundary`.",
        ]
    else:
        listed = "One row per labelled mitochondrion in the volume (exclusions removed)."
        scope = [
            "## Scope: the whole volume",
            "",
            "Every label in the volume is listed. No region mask was given, so the region columns are empty and "
            "`crosses_region_boundary` is always `false`.",
        ]
    if ctx["voxel_matches_metadata"]:
        voxel_note = f"The given voxel size matches the volume's metadata ({mz} × {my} × {mx} nm, Z, Y, X)."
    else:
        voxel_note = (f"**Voxel size override.** The volume's metadata says {mz} × {my} × {mx} nm (Z, Y, X), "
                      f"but the voxel size was given explicitly as {vz} × {vy} × {vx} nm and overrides it. Every "
                      "volume, length and centroid here uses the given size; the metadata value is not used.")
    box_text = "the volume's own voxel coordinates (as Neuroglancer shows them"
    box_text += (f", including its voxel offset {list(labels.offset)} (Z, Y, X))" if any(labels.offset) else ")")
    lines = [
        f"# Mitochondria: measurements of `{name}`",
        "",
        f"Generated {ctx['generated_at']} by `measure_volume` from `{labels.path}`.",
        "",
        *readme_files_lines(listed),
        f"In short: **{s_all['objects']:,} mitochondria**, of which **{s_core['objects']:,} are core** "
        f"(complete; see `core`). {ctx['excluded_count']:,} object(s) were excluded.",
        "",
        "## The input volume",
        "",
        f"`{name}` is one volume of {sx:,} × {sy:,} × {sz:,} voxels (x, y, z), read from `{labels.path}` "
        f"(Neuroglancer precomputed, scale 0, `{labels.encoding}`, `{labels.data_type}`; "
        f"{ctx['chunk_files']:,} chunk files). It was **stitched and seam-proofread upstream**; who did so is not "
        "recorded with the data, and this delivery neither repeats nor checks that work. Each label ID is taken "
        "as one mitochondrion.",
        "",
        voxel_note,
        "",
        *scope,
        "",
        *readme_units_lines(voxel, box_text),
        *readme_tables_lines(volume_column_docs(voxel, name, has_region=region is not None),
                             "Reviewers listed label IDs that are not mitochondria. Each listed ID removes that "
                             "label. Columns:",
                             {**EXCLUDED_DOCS,
                              "excluded_global_ids": "the reviewer-listed label ID (the column name is shared with "
                                                     "the stitched deliveries, where it holds global label IDs)",
                              "n_tiles": "always 1", "tiles": "the volume name"}),
        *readme_length_lines(f"Each mitochondrion is skeletonised once, as one piece, from a box around all of it "
                             f"(padded by {CROP_PAD} voxels and clipped to the volume).", ctx["max_crop_voxels"]),
        "## Constant columns",
        "",
        "These columns exist so that this delivery lines up column for column with the deliveries stitched "
        "from tiles (such as nag_p10). Here they never vary:",
        "",
        "- `n_tiles` is 1 and `tiles` is the volume name: the input is one volume, not tiles.",
        "- `touches_unproofread_tile` is `false`: there are no tiles with a recorded proofread status. The "
        "volume was proofread upstream as a whole.",
        "- Unresolved seam contacts are 0 (the `unresolved_contacts` metric in `summary.csv`): seams were "
        "proofread upstream, and this command makes no seam decisions. So `core` depends only on "
        "`cut_off_at_data_edge` and `crosses_region_boundary`.",
        "",
        "## Known limits",
        "",
        "- **Data edges.** Only the six outer faces of the volume count as the data edge. Chunk files that do "
        "not exist are read as background (Neuroglancer stores no all-zero chunks). If the upstream volume holds "
        "unimaged or missing parts as label 0 inside it, mitochondria touching them are not flagged.",
        "- **Labels are taken as given.** A mitochondrion split into two IDs upstream is two rows; two merged "
        "into one ID are one row.",
    ]
    if region is not None:
        lines += ["- **Outside the region** labels may not have been proofread; see *Scope*."]
    lines += [
        f"- **Too large to skeletonise:** {s_all['length_skipped_too_large']:,} listed (see `length_status`); "
        "their lengths are missing, not zero.",
        "",
        "## Provenance",
        "",
        f"- Delivery generated: {ctx['generated_at']}, code revision `{ctx['revision']}`, "
        f"command `{ctx['command']}`." if ctx["command"] else
        f"- Delivery generated: {ctx['generated_at']}, code revision `{ctx['revision']}`.",
        f"- Labels: `{labels.path}` (scale `{labels.key}`, metadata resolution {mx} × {my} × {mz} nm x, y, z).",
        f"- Region mask: `{region.path}` (scale `{region.key}`)." if region is not None else "- Region mask: none.",
        f"- Voxel size used: {vz} × {vy} × {vx} nm (Z, Y, X), given on the command line.",
        f"- Read with tensorstore {ctx['tensorstore_version']}. All settings are in `{MEASURE_RUN}` in the work "
        f"directory `{ctx['work']}`.",
        "",
    ]
    return "\n".join(lines)


# --- entry point -------------------------------------------------------------

@dataclass
class VolumeResult:
    out: Path
    rows: list[dict]
    excluded: list[dict]
    summary: list[dict]
    measurements: list[LabelMeasurement]
    run: dict
    counts: dict = field(default_factory=dict)


def _same(a, b) -> bool:
    return all(math.isclose(x, y, rel_tol=1e-9) for x, y in zip(a, b))


def run_measure_volume(labels, work, voxel_size_zyx, *, region=None, region_only: bool = False,
                       max_crop_voxels: int = DEFAULT_MAX_CROP_VOXELS, exclude_label_ids=None,
                       name: str | None = None, out=None, overwrite: bool = False, command: str = "",
                       generated_at: str | None = None, cache_bytes: int = CACHE_BYTES,
                       log=lambda message: None) -> VolumeResult:
    started_at = datetime.now(timezone.utc)
    try:
        voxel = validate_voxel_size(voxel_size_zyx)
    except MeasurementError as exc:
        raise VolumeMeasureError(str(exc)) from None
    if max_crop_voxels < 1:
        raise VolumeMeasureError("--max-crop-voxels must be positive")
    if region_only and region is None:
        raise VolumeMeasureError("--region-only needs --region")
    label_vol = read_info(labels, what="labels")
    if label_vol.data_type not in _LABEL_TYPES:
        raise VolumeMeasureError(f"labels: data type {label_vol.data_type}; expected one of {', '.join(_LABEL_TYPES)}")
    region_vol = read_info(region, what="region") if region is not None else None
    if region_vol is not None and (region_vol.size, region_vol.offset) != (label_vol.size, label_vol.offset):
        raise VolumeMeasureError(
            f"region grid does not match the labels: region size {region_vol.size[::-1]} offset "
            f"{region_vol.offset[::-1]}, labels size {label_vol.size[::-1]} offset {label_vol.offset[::-1]} "
            "(x, y, z voxels)")
    name = name or label_vol.path.name
    work = Path(work)
    out = Path(out) if out else work / "delivery"
    if out.exists() and any(out.iterdir()) and not overwrite:
        raise VolumeMeasureError(f"{out} is not empty; pass --overwrite to replace {', '.join(DELIVERY_FILES)}")
    if exclude_label_ids:
        read_exclusions(exclude_label_ids, _AnyLabel(), what="label id", source="the volume")

    matches = _same(label_vol.resolution, voxel)
    if not matches:
        log(f"voxel size {voxel} nm (Z, Y, X) overrides the labels' metadata {label_vol.resolution}")
    if region_vol is not None and not _same(region_vol.resolution, label_vol.resolution):
        log(f"warning: region metadata resolution {region_vol.resolution} differs from the labels' "
            f"{label_vol.resolution} (same voxel grid, so it is used as is)")

    store = open_store(label_vol, cache_bytes)
    region_store = open_store(region_vol, cache_bytes) if region_vol is not None else None
    chunks = list_chunks(label_vol)
    log(f"labels {label_vol.path}: {label_vol.size[::-1]} voxels (x, y, z), {len(chunks)} chunk file(s) of "
        f"{label_vol.chunk[::-1]}")
    t0 = time.monotonic()
    stats = scan_labels(store, chunks, region_store, log=log)
    pass1_seconds = time.monotonic() - t0
    work.mkdir(parents=True, exist_ok=True)
    write_csv(work / "label_stats.csv", LABEL_STATS_FIELDS if region_vol is not None else
              tuple(f for f in LABEL_STATS_FIELDS if f != REGION_VOXELS),
              [{"label_id": lid, "voxel_count": s.voxel_count,
                **({REGION_VOXELS: s.region_voxels} if region_vol is not None else {}),
                **dict(zip(BBOX_FIELDS, s.box))} for lid, s in sorted(stats.items())])

    excluded = (read_exclusions(exclude_label_ids, {lid: lid for lid in stats}, what="label id",
                                source="the volume") if exclude_label_ids else {})
    selected = [lid for lid in stats if lid not in excluded and (not region_only or stats[lid].region_voxels > 0)]
    # Chunk order, so neighbouring crops share decoded chunks in the cache.
    selected.sort(key=lambda lid: (*(stats[lid].box[2 * a] // label_vol.chunk[a] for a in range(3)), lid))
    log(f"pass 1 done in {pass1_seconds:.0f}s: {len(stats)} label(s); measuring {len(selected)}"
        + (" (reaching into the region)" if region_only else "") + (f", {len(excluded)} excluded" if excluded else ""))

    t0 = time.monotonic()
    measurements = []
    step = max(1, len(selected) // 20)
    for index, lid in enumerate(selected, 1):
        t1 = time.monotonic()
        m = measure_label(store, lid, stats[lid], label_vol.box, voxel, max_crop_voxels,
                          has_region=region_vol is not None)
        measurements.append(m)
        if m.skipped_reason:
            log(f"label {lid}: crop {box_voxels(padded_window(m.box, label_vol.box)):,} voxels > "
                f"{max_crop_voxels:,}; skipped (crop_limit)")
        elif time.monotonic() - t1 > 30:
            log(f"label {lid}: {m.skeleton_length_um:.4g} um in {time.monotonic() - t1:.0f}s")
        if index % step == 0 or index == len(selected):
            elapsed = time.monotonic() - t0
            log(f"pass 2: label {index}/{len(selected)}, {elapsed:.0f}s"
                + (f", about {elapsed / index * (len(selected) - index):.0f}s left" if index < len(selected) else ""))
    measurements.sort(key=lambda m: m.label_id)

    rows, flags = [], []
    for m in measurements:
        row, f = delivery_row(m, name, voxel)
        rows.append(row)
        flags.append(f)
    core = [i for i, r in enumerate(rows) if r["core"] == "true"]
    s_all = summarise(rows, flags)
    s_core = summarise([rows[i] for i in core], [flags[i] for i in core])
    summary = [{"metric": m, "all": s_all[m], "core": s_core[m]} for m in SUMMARY_METRICS]
    voxel_nm3 = voxel[0] * voxel[1] * voxel[2]
    excluded_rows = [{"mito_id": lid, "excluded_global_ids": lid, "n_tiles": 1, "tiles": name,
                      "volume_um3": repr(stats[lid].voxel_count * voxel_nm3 / 1e9),
                      "reaches_region": "true" if stats[lid].region_voxels > 0 else "false"}
                     for lid in sorted(excluded)]

    ts_version = tensorstore_version()
    revision = code_revision()
    counts = {
        "chunk_files": len(chunks), "labels": len(stats), "listed": len(rows), "excluded": len(excluded_rows),
        "core": len(core), "cut_off_at_data_edge": s_all["cut_off_at_data_edge"],
        "crosses_region_boundary": s_all["crosses_region_boundary"],
        "skipped_crop_limit": s_all["length_skipped_too_large"],
        "pass1_seconds": round(pass1_seconds), "pass2_seconds": round(time.monotonic() - t0),
    }
    run = {
        "command": command, "labels": label_vol.describe(),
        "region": region_vol.describe() if region_vol is not None else None,
        "volume_name": name, "voxel_size_zyx_nm": list(voxel),
        "voxel_size_matches_metadata": matches,
        "voxel_size_note": ("matches the labels' metadata" if matches else
                            f"given explicitly; overrides the labels' metadata resolution "
                            f"{list(label_vol.resolution)} nm (Z, Y, X)"),
        "region_only": region_only, "max_crop_voxels": max_crop_voxels, "crop_pad": CROP_PAD,
        "exclude_label_ids": str(Path(exclude_label_ids).resolve()) if exclude_label_ids else None,
        "excluded_label_ids": sorted(excluded), "unproofread_tiles": [], "delivery": str(out),
        "tensorstore_version": ts_version, "code_revision": revision,
        "started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "counts": counts,
    }
    ctx = {
        "voxel": voxel, "name": name, "labels": label_vol, "region": region_vol, "region_only": region_only,
        "summary_all": s_all, "summary_core": s_core, "excluded_count": len(excluded_rows),
        "voxel_matches_metadata": matches, "chunk_files": len(chunks), "max_crop_voxels": max_crop_voxels,
        "revision": revision, "command": command, "work": str(work), "tensorstore_version": ts_version,
        "generated_at": generated_at or started_at.strftime("%Y-%m-%d %H:%M UTC"),
    }
    (work / MEASURE_RUN).write_text(json.dumps(run, indent=2) + "\n")
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "mitochondria.csv", MITO_FIELDS, rows)
    write_csv(out / "summary.csv", SUMMARY_FIELDS, summary)
    write_csv(out / "excluded.csv", EXCLUDED_FIELDS, excluded_rows)
    (out / "README.md").write_text(build_volume_readme(ctx))
    return VolumeResult(out, rows, excluded_rows, summary, measurements, run, counts)
