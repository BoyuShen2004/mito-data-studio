"""Shared, dependency-light volume-inspection utilities.

Fast TIFF shape/voxel-size reading from headers, without loading pixel data.
These are deterministic and safe to call from views, services, management
commands, and future agent tools.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger(__name__)

# Header reads are cheap (7-80ms) but not free, and the same file's shape /
# voxel size is asked for repeatedly — per volume row on a detail page, and on
# every 3D mesh request for a volume whose size isn't recorded in the database.
# Keyed by (path, mtime) so replacing a file is picked up without any explicit
# invalidation.
_MAX_HEADER_CACHE = 256
_header_cache: "OrderedDict[tuple, object]" = OrderedDict()


def _header_cached(kind: str, path: Path, compute):
    try:
        key = (kind, str(path), path.stat().st_mtime)
    except OSError:
        return compute()
    if key in _header_cache:
        _header_cache.move_to_end(key)
        return _header_cache[key]
    value = compute()
    _header_cache[key] = value
    _header_cache.move_to_end(key)
    while len(_header_cache) > _MAX_HEADER_CACHE:
        _header_cache.popitem(last=False)
    return value


def clear_header_cache() -> None:
    """Forget every cached shape/voxel-size read (tests, and after a bulk
    re-registration that rewrote files in place)."""
    _header_cache.clear()


def array_shape_to_xyz(shape: tuple) -> tuple[int, int, int]:
    """Convert an array shape to ``(x, y, z)`` convention.

    - ``(z, y, x)`` -> ``(x, y, z)``
    - ``(y, x)`` -> ``(x, y, 1)``
    - more dims: use the last three as ``(z, y, x)``
    """
    if len(shape) == 2:
        y, x = shape
        return (int(x), int(y), 1)
    if len(shape) == 3:
        z, y, x = shape
        return (int(x), int(y), int(z))
    if len(shape) > 3:
        z, y, x = shape[-3], shape[-2], shape[-1]
        return (int(x), int(y), int(z))
    raise ValueError(f"Unsupported array shape: {shape}")


def read_tiff_shape_fast(path: str | Path) -> tuple[int, int, int]:
    """Read a TIFF's ``(x, y, z)`` shape from headers without loading the array."""
    import tifffile  # imported lazily so non-TIFF workflows don't need it

    with tifffile.TiffFile(str(path)) as tif:
        if tif.series:
            shape = tif.series[0].shape
        elif len(tif.pages) > 1:
            shape = (len(tif.pages),) + tif.pages[0].shape
        else:
            shape = tif.pages[0].shape
    return array_shape_to_xyz(shape)


def inspect_volume_shape(path: str | Path) -> tuple[int, int, int] | None:
    """Best-effort ``(x, y, z)`` shape for a supported volume file.

    Returns ``None`` if the shape cannot be determined (unsupported format,
    missing file, or a read error). TIFF needs no extra deps; HDF5 needs h5py
    and degrades to ``None`` without it, so a missing dependency skips the
    shape check rather than blocking registration.
    """
    p = Path(path)
    if not p.exists():
        return None
    suffix = p.suffix.lower()

    def compute():
        try:
            if suffix in {".tif", ".tiff"}:
                return read_tiff_shape_fast(p)
            from annotation.visualization.nifti_io import is_nifti_path, nifti_shape_xyz
            if is_nifti_path(p):
                return nifti_shape_xyz(p)
            from annotation.visualization.hdf5_io import hdf5_shape_xyz, is_hdf5_path

            if is_hdf5_path(p):
                return hdf5_shape_xyz(p)
        except Exception:
            # Swallowed on purpose: a header this function cannot read must not
            # fail registration. Logged at DEBUG because plenty of callers ask
            # speculatively about files that were never going to have a
            # readable header — the *actionable* warning (a registered volume
            # left with no shape, and therefore unassignable) is raised once
            # per volume by `volumes.services._try_autodetect_shape`.
            logger.debug("Could not read the shape of %s", p, exc_info=True)
            return None
        return None

    return _header_cached("shape", p, compute)


# OME-XML physical sizes carry their own unit per axis (default µm). Everything
# this function returns is normalised to **µm** so the three axes are always
# comparable — mixing a z in nm with an x/y in cm is how you get a "voxel size"
# with a 30000:1 aspect ratio and a 3D view that renders a mitochondrion as a
# sheet of paper.
_UM_PER_UNIT = {
    "m": 1e6,
    "dm": 1e5,
    "cm": 1e4,
    "mm": 1e3,
    "µm": 1.0,
    "um": 1.0,
    "μm": 1.0,  # U+03BC (greek small mu) — distinct codepoint from U+00B5
    "nm": 1e-3,
    "pm": 1e-6,
    "Å": 1e-4,
    "angstrom": 1e-4,
    "in": 25400.0,
    "inch": 25400.0,
}
# TIFF ResolutionUnit: 1 = none (a bare aspect ratio, NOT a physical size),
# 2 = inch, 3 = centimetre.
_RESOLUTION_UNIT_UM = {2: 25400.0, 3: 1e4}


def _um_per_unit(unit: str | None) -> float | None:
    """Return µm per unit, or ``None`` for unknown/empty spellings."""
    if unit is None:
        return None
    unit = unit.strip()
    if not unit:
        return None
    return _UM_PER_UNIT.get(unit) or _UM_PER_UNIT.get(unit.lower())


def _ome_voxel_size(tif) -> tuple[float | None, float | None, float | None] | None:
    """``(z, y, x)`` in µm from OME-XML's ``Pixels`` element, or ``None``.

    OME is preferred over the TIFF resolution tags whenever it is present: it
    is explicit about units per axis, whereas the resolution tags on the EM
    exports here carry rationals like ``(4828, 4294967295)`` that decode to
    physically meaningless spacings.
    """
    import xml.etree.ElementTree as ET

    ome = tif.ome_metadata
    if not ome:
        return None
    try:
        root = ET.fromstring(ome)
    except ET.ParseError:
        return None
    pixels = next((el for el in root.iter() if el.tag.rsplit("}", 1)[-1] == "Pixels"), None)
    if pixels is None:
        return None

    def axis(name: str) -> float | None:
        raw = pixels.attrib.get(f"PhysicalSize{name}")
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        if value <= 0:
            return None
        unit_attr = pixels.attrib.get(f"PhysicalSize{name}Unit")
        scale = _um_per_unit(unit_attr if unit_attr is not None else "µm")
        if scale is None:
            return None
        return value * scale

    z, y, x = axis("Z"), axis("Y"), axis("X")
    if z is None and y is None and x is None:
        return None
    return (z, y, x)


def _tiff_voxel_size(path: str | Path) -> tuple[float, float, float] | None:
    """Read a TIFF's ``(z, y, x)`` voxel size **in µm** from its headers.

    Two sources, tried whole rather than mixed (see ``_UM_PER_UNIT``):

    1. **OME-XML** (`PhysicalSizeZ/Y/X` + their units) — used as-is when
       present, including for axes it leaves out.
    2. **ImageJ metadata + TIFF resolution tags** — ImageJ records the
       z-spacing, the standard ``XResolution``/``YResolution`` tags the
       in-plane spacing (pixels per unit → spacing = 1/res, scaled by
       ``ResolutionUnit``). A file with ``ResolutionUnit = none`` records an
       aspect ratio, not a size, and is ignored.

    Returns ``None`` when nothing usable is found; individual axes may be
    ``None`` when only some are recorded.
    """
    import tifffile

    with tifffile.TiffFile(str(path)) as tif:
        ome = _ome_voxel_size(tif)
        if ome is not None:
            z, y, x = ome
            return (z, y, x)

        page = tif.pages[0]
        ij = tif.imagej_metadata or {}
        if ij:
            # ImageJ writes ResolutionUnit = none by design and names the unit
            # in its own metadata block; that one unit covers all three axes.
            raw_unit = ij.get("unit")
            um_per_unit = _um_per_unit(
                str(raw_unit) if raw_unit is not None else None
            )
        else:
            unit_tag = page.tags.get("ResolutionUnit")
            if unit_tag is None:
                um_per_unit = None
            else:
                unit_code = int(unit_tag.value)
                um_per_unit = _RESOLUTION_UNIT_UM.get(unit_code)

        def _spacing_from_tag(tag_name: str) -> float | None:
            if um_per_unit is None:
                return None  # ResolutionUnit = none → ratio only, not a size
            tag = page.tags.get(tag_name)
            if not tag or not tag.value:
                return None
            value = tag.value
            # Resolution tags are RATIONAL (numerator, denominator) = px/unit.
            if isinstance(value, tuple) and len(value) == 2 and value[0]:
                num, den = value
                return (den / num) * um_per_unit
            if value:
                return (1.0 / float(value)) * um_per_unit
            return None

        z = ij.get("spacing")
        z = (
            float(z) * um_per_unit
            if um_per_unit is not None and isinstance(z, (int, float)) and z
            else None
        )
        y = _spacing_from_tag("YResolution")
        x = _spacing_from_tag("XResolution")

    if z is None and y is None and x is None:
        return None
    return (z, y, x)


def inspect_volume_voxel_size(
    path: str | Path,
) -> tuple[float, float, float] | None:
    """Best-effort ``(z, y, x)`` voxel size for a supported volume file.

    Reads the physical spacing recorded in the file's headers (TIFF resolution
    tags / ImageJ metadata; NIfTI pixdim when nibabel is available). Returns
    ``None`` when it cannot be determined, so callers can leave the field blank.
    """
    p = Path(path)
    if not p.exists():
        return None
    name = p.name.lower()

    def compute():
        try:
            if name.endswith((".tif", ".tiff")):
                return _tiff_voxel_size(p)
            if name.endswith((".nii", ".nii.gz")):
                from annotation.visualization.nifti_io import nifti_voxel_size_zyx
                return nifti_voxel_size_zyx(p)
            from annotation.visualization.hdf5_io import (
                hdf5_voxel_size_zyx,
                is_hdf5_path,
            )

            if is_hdf5_path(p):
                return hdf5_voxel_size_zyx(p)
        except Exception:
            return None
        return None

    return _header_cached("voxel", p, compute)


def inspect_volume_dtype(path: str | Path) -> str | None:
    """Best-effort source dtype from format headers, without loading voxels."""
    p = Path(path)
    if not p.exists():
        return None

    def compute():
        try:
            name = p.name.lower()
            if name.endswith((".tif", ".tiff")):
                import tifffile
                with tifffile.TiffFile(str(p)) as tif:
                    return str(tif.series[0].dtype)
            from annotation.visualization.hdf5_io import is_hdf5_path, open_hdf5_volume
            if is_hdf5_path(p):
                return str(open_hdf5_volume(p).dtype)
            from annotation.visualization.nifti_io import is_nifti_path, open_nifti_volume
            if is_nifti_path(p):
                return str(open_nifti_volume(p).dtype)
        except Exception:
            logger.debug("Could not read dtype of %s", p, exc_info=True)
        return None

    return _header_cached("dtype", p, compute)
