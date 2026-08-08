"""Lazy, read-only NIfTI adapter exposing the application's ``(Z,Y,X)`` axes.

Nibabel stores NIfTI arrays as ``(X,Y,Z)``.  Keeping its ArrayProxy alive
avoids materialising an uncompressed ``.nii`` volume and limits ``.nii.gz``
work to the planes/crops requested by the shared slice API.
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
        xyz = source_shape[:3] if len(source_shape) >= 3 else (*source_shape, 1)
        self.shape = (xyz[2], xyz[1], xyz[0])
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
        xyz_key = (zyx_key[2], zyx_key[1], zyx_key[0])
        if len(self.image.shape) > 3:
            xyz_key += (0,) * (len(self.image.shape) - 3)
        value = np.asanyarray(self.image.dataobj[xyz_key])
        if value.ndim <= 1:
            return value
        remaining_xyz = [axis for axis, item in zip("xyz", xyz_key[:3]) if not isinstance(item, (int, np.integer))]
        desired = [axis for axis in "zyx" if axis in remaining_xyz]
        permutation = [remaining_xyz.index(axis) for axis in desired]
        return value.transpose(permutation) if permutation != list(range(value.ndim)) else value

    def __array__(self, dtype=None, copy=None):
        value = np.asanyarray(self.image.dataobj)
        if value.ndim > 3:
            value = value[(slice(None), slice(None), slice(None)) + (0,) * (value.ndim - 3)]
        if value.ndim == 2:
            value = value[:, :, np.newaxis]
        value = value.transpose(2, 1, 0)
        return np.asarray(value, dtype=dtype) if dtype is not None else np.asarray(value)


def open_nifti_volume(path: str | Path) -> NiftiVolume:
    return NiftiVolume(path)


def nifti_shape_xyz(path: str | Path) -> tuple[int, int, int]:
    volume = NiftiVolume(path)
    z, y, x = volume.shape
    return (x, y, z)


def nifti_voxel_size_zyx(path: str | Path):
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
    x, y, z = (float(zooms[i]) * scale for i in range(3))
    return (z, y, x)
