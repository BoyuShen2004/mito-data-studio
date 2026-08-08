"""SAM2 GPU tracking adapter (HPC compute node).

Ports the MTS SAM2 approach (``sam2_bridge.py`` in this package, itself a
port of ``MTS/mts_mask_editor/core/sam2_wrapper.py`` + ``track_propagation.
py``): each fork branch is registered as its own SAM2 object id via
``add_mask_prompt`` and propagated with ``propagate_multi`` in both
directions. The model is heavy and GPU-only. The current Track batch endpoint
invokes this adapter synchronously in a gunicorn worker, so the provider is a
process-local singleton and serializes access to SAM2's mutable inference
state. ``torch``/``sam2`` are imported lazily (inside ``sam2_bridge.py``, not
here) so importing this module (and running the rest of the app / tests) never
requires them.

The actual SAM 2 model + weights are vendored under ``vendor/sam2/`` (see
``README.md``) rather than referencing an external checkout —
``MITO_SAM2_ROOT`` defaults to that vendored copy (``config/settings.py``),
so this provider works out of the box wherever this repo is checked out,
no sibling ``MTS`` directory required.
"""

from __future__ import annotations

import logging
import threading
import time

from django.conf import settings

from .. import xy_crop
from ..interfaces import PropagationRequest, PropagationResult, TrackingProvider

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("mito.track.timing")


class Sam2TrackingProvider(TrackingProvider):
    name = "sam2"
    requires_gpu = True

    def __init__(self):
        self._sam = None
        # SAM2's predictor/inference_state is mutable. A process singleton is
        # safe only when one propagation owns it at a time (gunicorn uses two
        # threads per worker in this deployment).
        self._lock = threading.RLock()

    def _load(self):
        with self._lock:
            if self._sam is not None:
                return self._sam
            # Imported here (not at module scope) so non-GPU environments never
            # pull torch/sam2 in just by importing this adapters module.
            import torch
            from .sam2_bridge import SAM2Wrapper

            if not torch.cuda.is_available():
                raise RuntimeError(
                    "MITO_TRACKING_PROVIDER=sam2 requires CUDA, but this process "
                    "reports torch.cuda.is_available()=False. Check the CUDA "
                    "wheel and service GPU visibility; CPU fallback is disabled."
                )
            device_id = int(getattr(settings, "MITO_SAM2_CUDA_DEVICE", 0))
            if not 0 <= device_id < torch.cuda.device_count():
                raise RuntimeError(
                    f"MITO_SAM2_CUDA_DEVICE={device_id} is outside the "
                    f"{torch.cuda.device_count()} visible CUDA device(s)."
                )
            sam2_root = getattr(settings, "MITO_SAM2_ROOT", "")
            if not sam2_root:
                raise RuntimeError(
                    "MITO_SAM2_ROOT is not set and has no default — this should "
                    "not happen unless config/settings.py was changed; see "
                    "progress/development.md's SAM2 section."
                )
            checkpoint = getattr(settings, "MITO_SAM2_CHECKPOINT", "") or None
            config = getattr(settings, "MITO_SAM2_CONFIG", "") or None
            device = f"cuda:{device_id}"
            logger.info(
                "SAM2 runtime cuda_available=%s cuda_count=%d device=%s "
                "device_name=%s checkpoint=%s config=%s",
                torch.cuda.is_available(),
                torch.cuda.device_count(),
                device,
                torch.cuda.get_device_name(device_id),
                checkpoint,
                config,
            )
            started = time.perf_counter()
            self._sam = SAM2Wrapper(
                sam2_root=sam2_root,
                checkpoint=checkpoint,
                config=config,
                device=device,
            )
            timing_logger.info(
                "sam2 model_load_ms=%.1f device=%s checkpoint=%s",
                (time.perf_counter() - started) * 1000.0,
                self._sam.device,
                self._sam.checkpoint.name,
            )
            return self._sam

    def _propagate_crop(self, sam, image_zyx, seeds, z_lo):
        import numpy as np

        if not seeds:
            return PropagationResult()
        started = time.perf_counter()
        sam.reset_session()
        sam.initialize_sequence(image_zyx)
        initialized = time.perf_counter()
        result = PropagationResult()
        seeded_local = []
        for branch_id, seed_slices in seeds.items():
            branch_id = int(branch_id)
            for seed_z, mask in seed_slices.items():
                local_z = int(seed_z) - z_lo
                if not 0 <= local_z < image_zyx.shape[0]:
                    raise ValueError(f"Seed z={seed_z} is outside tracking range")
                mask = np.asarray(mask, dtype=bool)
                if mask.shape != image_zyx.shape[1:]:
                    raise ValueError(
                        f"Seed mask shape {mask.shape} does not match image plane "
                        f"{image_zyx.shape[1:]}"
                    )
                sam.add_mask_prompt(local_z, obj_id=branch_id, mask=mask)
                seeded_local.append(local_z)
                result.masks.setdefault(branch_id, {})[int(seed_z)] = mask
        raw = sam.propagate_multi(
            min(seeded_local),
            z_range=(0, image_zyx.shape[0] - 1),
            direction="both",
            backward_start_slice=max(seeded_local),
        )
        propagated = time.perf_counter()
        for object_id, per_z in raw.items():
            destination = result.masks.setdefault(int(object_id), {})
            for local_z, mask in per_z.items():
                destination[int(local_z) + z_lo] = np.asarray(mask, dtype=bool)
        timing_logger.info(
            "sam2 crop_shape=%s initialize_ms=%.1f propagate_ms=%.1f total_ms=%.1f",
            tuple(int(v) for v in image_zyx.shape),
            (initialized - started) * 1000.0,
            (propagated - initialized) * 1000.0,
            (time.perf_counter() - started) * 1000.0,
        )
        return result

    def propagate(self, request: PropagationRequest) -> PropagationResult:
        import numpy as np

        with self._lock:
            sam = self._load()
            z_lo, z_hi = request.z_range
            if z_hi < z_lo:
                raise ValueError(f"Invalid z_range {(z_lo, z_hi)}")
            stack = np.asarray(request.image[z_lo : z_hi + 1])
            if stack.ndim != 3 or not stack.shape[0]:
                raise ValueError("Tracking image crop is empty")
            height, width = stack.shape[1:]
            roi = xy_crop.plan_xy_roi(request.seeds, height, width)
            logger.info(
                "SAM2 propagation z_range=%s source_shape=%s initial_xy_roi=%s",
                (z_lo, z_hi),
                tuple(int(v) for v in stack.shape),
                roi.cache_token(),
            )
            attempted = set()
            while True:
                attempted.add(roi.cache_token())
                if roi.covers(height, width):
                    crop_stack, seeds = stack, request.seeds
                else:
                    crop_stack = xy_crop.crop_stack(stack, roi)
                    seeds = xy_crop.crop_seeds(request.seeds, roi)
                result = self._propagate_crop(sam, crop_stack, seeds, z_lo)
                if roi.covers(height, width):
                    return result
                expanded = xy_crop.maybe_expand_for_border(
                    roi, height, width, result.masks
                )
                if expanded is None or expanded.cache_token() in attempted:
                    return PropagationResult(
                        masks=xy_crop.paste_masks(
                            result.masks, roi, (height, width)
                        )
                    )
                roi = expanded
