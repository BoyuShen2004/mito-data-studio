"""Lazy, read-only NIfTI adapter exposing the application's ``(Z,Y,X)`` axes.

On-disk NIfTI arrays are treated as ``(Z,Y,X)`` — the same axis order as HDF5
and TIFF in this application. Keeping nibabel's ArrayProxy alive avoids
materialising an uncompressed ``.nii`` volume and limits ``.nii.gz`` work to
the planes/crops requested by the shared slice API.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


class NiftiError(ValueError):
    pass


def is_nifti_path(path: str | Path) -> bool:
    name = str(path).lower()
    return name.endswith(".nii") or name.endswith(".nii.gz")


class NiftiVolume:
    def __init__(self, path: str | Path):
        try:
            import nibabel as nib

            self.image = nib.load(str(path), mmap=True, keep_file_open=True)
        except Exception as exc:
            raise NiftiError(f"Could not open NIfTI volume {Path(path).name}: {exc}") from exc
        source_shape = tuple(int(v) for v in self.image.shape)
        if len(source_shape) < 2:
            raise NiftiError(f"NIfTI volume must be 2D or 3D, got {source_shape}.")
        if len(source_shape) > 3 and any(v != 1 for v in source_shape[3:]):
            raise NiftiError(
                f"Multi-channel/time NIfTI shape {source_shape} is ambiguous; "
                "register one 3D channel per volume."
            )
        if len(source_shape) == 2:
            # Match TIFF/HDF5: a single plane is exposed as (1, Y, X).
            self.shape = (1, source_shape[0], source_shape[1])
        else:
            self.shape = source_shape[:3]
        self.dtype = np.dtype(self.image.dataobj.dtype)
        self.ndim = 3

    @property
    def size(self) -> int:
        return int(np.prod(self.shape))

    def max(self):
        maximum = None
        for z in range(self.shape[0]):
            plane_max = np.asarray(self[z]).max()
            maximum = plane_max if maximum is None else max(maximum, plane_max)
        return 0 if maximum is None else maximum

    def __getitem__(self, key):
        if not isinstance(key, tuple):
            key = (key,)
        zyx_key = tuple(key) + (slice(None),) * (3 - len(key))
        source_ndim = len(self.image.shape)
        if source_ndim == 2:
            z_item, y_item, x_item = zyx_key
            if isinstance(z_item, (int, np.integer)):
                if int(z_item) != 0:
                    raise IndexError(
                        f"2D NIfTI only has z=0; got z={int(z_item)}"
                    )
                source_key = (y_item, x_item)
            else:
                # A z-slice over the sole plane still yields a leading axis of
                # length 1 so callers see (Z,Y,X)-shaped results.
                plane = np.asanyarray(self.image.dataobj[(y_item, x_item)])
                return plane[np.newaxis, ...]
        else:
            source_key = zyx_key
            if source_ndim > 3:
                source_key = zyx_key + (0,) * (source_ndim - 3)
        return np.asanyarray(self.image.dataobj[source_key])

    def __array__(self, dtype=None, copy=None):
        value = np.asanyarray(self.image.dataobj)
        if value.ndim > 3:
            value = value[(slice(None), slice(None), slice(None)) + (0,) * (value.ndim - 3)]
        if value.ndim == 2:
            value = value[np.newaxis, ...]
        return np.asarray(value, dtype=dtype) if dtype is not None else np.asarray(value)


def open_nifti_volume(path: str | Path) -> NiftiVolume:
    return NiftiVolume(path)


def nifti_shape_xyz(path: str | Path) -> tuple[int, int, int]:
    """``(x, y, z)`` from headers — same contract as ``hdf5_shape_xyz``."""
    volume = NiftiVolume(path)
    z, y, x = volume.shape
    return (x, y, z)


def nifti_voxel_size_zyx(path: str | Path):
    """``(z, y, x)`` voxel size in µm from NIfTI pixdim.

    Pixdim is read in on-disk array order, which this adapter treats as
    ``(Z,Y,X)`` to match HDF5 ``element_size_um`` and TIFF ImageJ spacing.
    """
    volume = NiftiVolume(path)
    header = volume.image.header
    unit = (header.get_xyzt_units()[0] or "").lower()
    scales = {"micron": 1.0, "mm": 1000.0, "meter": 1_000_000.0}
    if unit not in scales:
        return None
    zooms = header.get_zooms()
    if len(zooms) < 3:
        return None
    scale = scales[unit]
    z, y, x = (float(zooms[i]) * scale for i in range(3))
    return (z, y, x)
