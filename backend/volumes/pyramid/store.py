"""Where a derivative lives, and the Zarr v3 contract it is written in.

ADR-009 §3 locks this layout, and **Phase 12 may rely on it**:

    <MITO_DATA_ROOT>/<project>/<dataset>/pyramids/<image-stem>.zarr/
        zarr.json          group metadata, zarr_format: 3
        1/ 2/ 4/ 8/ …      one array per mag, named by the xy factor

Beside the volume rather than in a global silo, for the same reason
``label_paths`` puts masks, metadata and embeddings there: everything belonging
to one volume is in one place, and browsing ``data/`` matches what the app shows.

**Layers** (ADR-009 addendum). The read-only region mask gets the same treatment
in a *sibling group*, ``<image-stem>.region.zarr``, rather than extra arrays
inside the image group. One group per layer keeps every existing derivative
valid without a rewrite, lets a layer be rebuilt or rolled back on its own, and
means the serving path's cache key is a distinct store handle per layer instead
of one handle whose contents depend on which array is asked for. Editable labels
are deliberately absent: a mutable layer needs a sparse write store, not a
static pyramid.

zarr is imported **lazily**, inside the functions that need it. The feature is
off by default and the deployment environment does not have zarr installed; a
module-level import would make an unrelated deployment fail to start, which is
exactly the class of breakage the data-root work spent a phase eliminating.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from core.data_root import assert_owned

from .ladder import MagLevel, chunk_shape_for

#: Bump if the on-disk contract changes in a way a reader must notice.
PYRAMID_LAYOUT_VERSION = 1

ZARR_MIN = (3, 1)

#: The raw image — the layer every volume has.
LAYER_IMAGE = "image"
#: The immutable region mask (ROI). Present only when the volume has one.
LAYER_REGION = "region"
#: Every layer a derivative may exist for. Editable labels are not one of them.
LAYERS = (LAYER_IMAGE, LAYER_REGION)

#: Per-layer model fields, so callers never spell readiness twice.
LAYER_FIELDS = {
    LAYER_IMAGE: ("ready_streaming", "pyramid_metadata"),
    LAYER_REGION: ("region_ready_streaming", "region_pyramid_metadata"),
}


class PyramidStoreError(RuntimeError):
    """The derivative could not be written or read."""


def require_layer(layer: str) -> str:
    """Normalise and check a layer name."""
    name = str(layer or LAYER_IMAGE)
    if name not in LAYERS:
        raise PyramidStoreError(
            f"Unknown pyramid layer {layer!r}; expected one of {', '.join(LAYERS)}."
        )
    return name


def layer_source(volume, layer: str = LAYER_IMAGE) -> str:
    """The source location a layer is built from ("" when the volume has none)."""
    if require_layer(layer) == LAYER_REGION:
        return volume.region_mask_location
    return volume.image_location


def layer_ready(volume, layer: str = LAYER_IMAGE) -> bool:
    """Whether a validated derivative exists for this layer."""
    return bool(getattr(volume, LAYER_FIELDS[require_layer(layer)][0]))


def layer_metadata(volume, layer: str = LAYER_IMAGE) -> dict:
    """What was recorded at promotion time (path, mags, built_at)."""
    return getattr(volume, LAYER_FIELDS[require_layer(layer)][1]) or {}


def require_zarr():
    """Import zarr, or explain precisely what is missing.

    ``zarr==3.0.6`` is yanked upstream (``zarr.load()`` deletes data), so the
    floor is 3.1; the ceiling is enforced by the environment pin rather than
    here, since a major bump would change this module, not just its version
    check.
    """
    try:
        import zarr  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise PyramidStoreError(
            "Building a volume pyramid needs zarr-python >= 3.1. It is an "
            "optional dependency: install it (see environment.yml) or leave "
            "FEATURE_VOLUME_PYRAMIDS off."
        ) from exc

    version = tuple(int(p) for p in zarr.__version__.split(".")[:2])
    if version < ZARR_MIN:
        raise PyramidStoreError(
            f"zarr {zarr.__version__} is too old; >= 3.1 is required "
            "(3.0.6 is yanked upstream for a data-loss bug)."
        )
    return zarr


@dataclass(frozen=True)
class PyramidLocation:
    """Where a volume's derivative lives."""

    rel_path: str
    path: Path

    @property
    def tmp_path(self) -> Path:
        """Sibling the build writes into before promotion.

        A derivative is moved into place only after validation, so a crashed
        build never leaves a half-pyramid that looks finished.
        """
        return self.path.with_name(self.path.name + ".building")


def pyramid_rel_path(volume, layer: str = LAYER_IMAGE) -> str:
    """``<project>/<dataset>/pyramids/<image stem>[.region].zarr`` — root-relative.

    The image layer keeps the original name, so every derivative built before
    layers existed stays exactly where it is.
    """
    from annotation.label_paths import dataset_folder_rel_path, working_mask_stem

    # `working_mask_stem` already carries the collision rule the masks use: two
    # volumes in one dataset folder deriving the same image stem get `_v<id>`.
    # Reusing it means a derivative can never land on another volume's path,
    # and the pyramid sits under the same stem as that volume's mask.
    dataset_dir = dataset_folder_rel_path(volume.project, volume.dataset)
    suffix = ".region" if require_layer(layer) == LAYER_REGION else ""
    return f"{dataset_dir}/pyramids/{working_mask_stem(volume)}{suffix}.zarr"


def pyramid_location(volume, layer: str = LAYER_IMAGE) -> PyramidLocation:
    from annotation.visualization.slice_io import resolve_path

    rel = pyramid_rel_path(volume, layer)
    return PyramidLocation(rel_path=rel, path=resolve_path(rel))


def create_group(path: Path, *, attributes: dict):
    """Create (or replace) a Zarr v3 group at ``path``."""
    zarr = require_zarr()
    assert_owned(path, what="pyramid group")
    path.parent.mkdir(parents=True, exist_ok=True)
    return zarr.create_group(store=str(path), zarr_format=3, overwrite=True, attributes=attributes)


def create_level_array(group, level: MagLevel, dtype, *, voxel_size, plane: int = 512):
    """Create the array for one mag, with its per-axis factors recorded."""
    chunks = chunk_shape_for(level.shape, plane=plane)
    return group.create_array(
        level.name,
        shape=level.shape,
        chunks=chunks,
        dtype=dtype,
        attributes={
            "level": level.level,
            "factors": list(level.factors),
            "voxel_size": [float(v) for v in voxel_size],
            "layout_version": PYRAMID_LAYOUT_VERSION,
        },
    )


def open_pyramid_at(rel_path: str, *, mode: str = "r"):
    """Open a derivative by its **recorded** relative path.

    Exists so the serving path never re-derives the location. Deriving it calls
    `working_mask_stem`, whose collision rule queries sibling volumes — one
    database round trip per chunk read, which is exactly the per-request ORM
    work ADR-010 §1 argues must not be on this path. The path is already stored
    in `Volume.pyramid_metadata` when the derivative is promoted, so serving
    reads it from there.
    """
    zarr = require_zarr()
    from annotation.visualization.slice_io import resolve_path

    path = resolve_path(rel_path)
    assert_owned(path, what="pyramid group")
    if not path.exists():
        raise PyramidStoreError("No pyramid at the recorded path.")
    return zarr.open_group(str(path), mode=mode)


def open_pyramid(volume, *, layer: str = LAYER_IMAGE, mode: str = "r"):
    """Open a volume's derivative group for one layer, or raise if it is absent."""
    zarr = require_zarr()
    location = pyramid_location(volume, layer)
    if not location.path.exists():
        raise PyramidStoreError(f"No pyramid at {location.rel_path}.")
    return zarr.open_group(str(location.path), mode=mode)


def read_plane(volume, mag: str, index: int, *, layer: str = LAYER_IMAGE) -> np.ndarray:
    """One z-plane of a mag, as a 2-D array.

    The read this whole phase exists to make cheap: at mag *k* the plane is
    k² smaller, and slice-oriented chunking means only that plane's tiles are
    touched.
    """
    group = open_pyramid(volume, layer=layer)
    if mag not in group:
        raise PyramidStoreError(f"Pyramid has no mag {mag!r}.")
    array = group[mag]
    depth = array.shape[0]
    if index < 0 or index >= depth:
        raise PyramidStoreError(
            f"index {index} out of range for mag {mag!r} (0..{depth - 1})."
        )
    return np.asarray(array[index])


def group_attributes(volume, *, layer: str = LAYER_IMAGE) -> dict:
    group = open_pyramid(volume, layer=layer)
    return dict(group.attrs)


def build_attributes(
    *,
    source_rel: str,
    dtype: str,
    voxel_size,
    ladder,
    reduction: str,
    checksum_seed: int,
    layer: str = LAYER_IMAGE,
) -> dict:
    """Self-describing group metadata, so a derivative can be audited on disk."""
    from .ladder import ladder_summary

    return {
        "layout_version": PYRAMID_LAYOUT_VERSION,
        "axes": ["z", "y", "x"],
        "layer": require_layer(layer),
        "source": source_rel,
        "dtype": str(dtype),
        "voxel_size": [float(v) for v in voxel_size],
        "reduction": reduction,
        "checksum_seed": int(checksum_seed),
        "built_at": datetime.now(timezone.utc).isoformat(),
        **ladder_summary(ladder),
    }


def digest(array: np.ndarray) -> str:
    """Stable digest of an array's bytes, for checksum validation."""
    contiguous = np.ascontiguousarray(array)
    return hashlib.sha256(contiguous.tobytes()).hexdigest()


def promote(location: PyramidLocation) -> None:
    """Move a validated build into place, replacing any previous derivative.

    Atomic enough for the guarantee that matters: a reader either sees the old
    derivative or the new one, never a partially written directory.
    """
    assert_owned(location.path, what="pyramid group")
    if not location.tmp_path.exists():
        raise PyramidStoreError("Nothing to promote — the build directory is gone.")
    if location.path.exists():
        retired = location.path.with_name(location.path.name + ".previous")
        if retired.exists():
            _remove_tree(retired)
        location.path.rename(retired)
        location.tmp_path.rename(location.path)
        _remove_tree(retired)
    else:
        location.tmp_path.rename(location.path)


def discard_build(location: PyramidLocation) -> None:
    """Remove a failed build. The *source* is never touched."""
    _remove_tree(location.tmp_path)


def _remove_tree(path: Path) -> None:
    import shutil

    if path.exists():
        assert_owned(path, what="pyramid directory")
        shutil.rmtree(path, ignore_errors=True)


def store_usage(volume, *, layer: str = LAYER_IMAGE) -> dict:
    """Size on disk, for the job record and the benchmark."""
    location = pyramid_location(volume, layer)
    if not location.path.exists():
        return {"exists": False, "bytes": 0, "files": 0}
    total = files = 0
    for entry in location.path.rglob("*"):
        if entry.is_file():
            total += entry.stat().st_size
            files += 1
    return {"exists": True, "bytes": total, "files": files, "path": location.rel_path}


def describe(volume, *, layer: str = LAYER_IMAGE) -> dict:
    """What the database records about a built derivative (no voxels)."""
    location = pyramid_location(volume, layer)
    if not location.path.exists():
        return {}
    attrs = group_attributes(volume, layer=layer)
    return {
        "path": location.rel_path,
        "layer": require_layer(layer),
        "layout_version": attrs.get("layout_version"),
        "axes": attrs.get("axes"),
        "dtype": attrs.get("dtype"),
        "levels": attrs.get("levels"),
        "reduction": attrs.get("reduction"),
        "built_at": attrs.get("built_at"),
        "usage": store_usage(volume, layer=layer),
    }


def dump_metadata(path: Path) -> dict:
    """Read a group's ``zarr.json`` directly — used by tests to prove the
    on-disk contract without going through zarr's reader."""
    with open(path / "zarr.json") as fh:
        return json.load(fh)
