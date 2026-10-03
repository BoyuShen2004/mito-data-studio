"""Resolve measurement spacing without modifying volume metadata.

The existing volume/header contract is micrometres. The skeleton engine uses
nanometres; that conversion belongs at the measurement boundary, not in storage.
"""
import math

from core.utils import inspect_volume_voxel_size
from volumes.services import volume_image_file


def _positive(value):
    return value is not None and math.isfinite(value) and value > 0


def measurement_spacing(volume):
    values = [volume.voxel_size_z, volume.voxel_size_y, volume.voxel_size_x]
    origins = ["registered" if _positive(value) else "unknown" for value in values]
    values = [value if _positive(value) else None for value in values]
    if None in values:
        try:
            path = volume_image_file(volume)
            detected = inspect_volume_voxel_size(path) if path else None
        except (OSError, ValueError):
            detected = None
        if detected:
            for axis, value in enumerate(detected):
                if values[axis] is None and _positive(value):
                    values[axis] = value
                    origins[axis] = "source_file"
    return {"voxel_size_um_zyx": values, "origins": origins}
