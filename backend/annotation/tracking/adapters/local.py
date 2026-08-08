"""Local CPU tracking adapter — a dependency-free stand-in for SAM2.

Propagates each seed forward and backward through the z-range by carrying the
seed slice's mask to neighbouring slices where the image is similar (a simple
intensity-threshold flood, not a learned model). It exists so the whole
fork → group → merge pipeline, the APIs, and the UI can be exercised in dev and
CI without a GPU. Swap in the ``sam2`` provider on a GPU node for real results.
"""

from __future__ import annotations

import numpy as np

from ..interfaces import PropagationRequest, PropagationResult, TrackingProvider


class LocalTrackingProvider(TrackingProvider):
    name = "local"
    requires_gpu = False

    def propagate(self, request: PropagationRequest) -> PropagationResult:
        z_lo, z_hi = request.z_range
        result = PropagationResult()
        for branch_id, seed_slices in request.seeds.items():
            per_z: dict[int, np.ndarray] = {}
            for seed_z, seed_mask in seed_slices.items():
                mask = np.asarray(seed_mask, dtype=bool)
                per_z[seed_z] = mask
                # Carry the seed to neighbours while it still overlaps foreground.
                self._carry(request.image, mask, seed_z, z_lo, seed_z - 1, -1, per_z)
                self._carry(request.image, mask, seed_z, seed_z + 1, z_hi, +1, per_z)
            result.masks[branch_id] = per_z
        return result

    @staticmethod
    def _carry(image, seed_mask, seed_z, start, stop, step, out):
        if seed_mask.sum() == 0:
            return
        # Foreground threshold from the seed's own intensities on its slice.
        seed_vals = image[seed_z][seed_mask]
        thresh = float(seed_vals.mean()) * 0.5 if seed_vals.size else 0.0
        prev = seed_mask
        z = start
        while (step > 0 and z <= stop) or (step < 0 and z >= stop):
            candidate = prev & (image[z] >= thresh)
            if candidate.sum() == 0:
                break
            out[z] = candidate
            prev = candidate
            z += step
