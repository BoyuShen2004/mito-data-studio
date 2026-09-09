"""SAM 2 backend for the interactive mask tools (Point / Box / Boundary).

EfficientSAM is small, CPU-only and cached, which is why it was the original
backend. It is also, measurably, not strong enough on every EM volume. Over
120 prompts at ground-truth object interiors across the four `me2-*` volumes,
one positive click scored (mean IoU against the annotated object):

    volume    EfficientSAM   SAM 2
    stem          0.638      0.650
    beta          0.571      0.658
    jurkat        0.738      0.777
    podo          0.267      0.485

The gap tracks object size: podo's mitochondria average 4327 px per slice
against stem's 1177 px, they cover 71% of the plane, and they touch each
other. EfficientSAM answers a click inside one of them with a fragment, and
no choice of its three mask candidates fixes that — the best any oracle could
do on podo was 0.363. That is a capability limit, not a selection bug, which
is why this module swaps the model rather than the ranking.

This provider keeps the three-method shape the application layer already
expected of a segmenter, including the same small-object cleanup, so the swap
changed mask *quality* and nothing else about the contract.

The SAM 2 checkpoint is shared with Track's propagation provider rather than
loaded again: it is ~2 GB resident on the GPU and loading a second copy per
process would be the expensive mistake this module is trying to avoid.
"""

from __future__ import annotations

from pathlib import Path
import threading

import numpy as np


class Sam2Masks:
    """`EfficientSam`-shaped adapter over Track's loaded SAM 2 image model."""

    def __init__(self, wrapper, lock=None):
        self._sam = wrapper
        # SAM 2's image predictor holds one slice's encoder features at a time,
        # so two threads prompting different slices must not interleave — this
        # deployment runs two gunicorn threads per worker.
        #
        # This is *not* tracking's lock. Weights are read-only during a forward
        # pass, so an Annotate prompt and a Track propagation can safely run at
        # once on the same model; serialising them instead would park a click
        # behind a propagation that owns its lock for minutes.
        self._lock = lock or threading.RLock()

    @staticmethod
    def _cache_key(disk_path) -> str | None:
        """`disk_path` already identifies (volume, axis, index, variant, mtime)
        — so it identifies the image without hashing it. It is also the L2
        file path (see `sam2_feature_cache`)."""
        return str(disk_path) if disk_path else None

    def _predict(self, image, *, points=None, point_labels=None, box=None, disk_path=None):
        image = np.asarray(image)
        with self._lock:
            mask = self._sam.predict_single_frame(
                image,
                points=points,
                point_labels=point_labels,
                box=box,
                cache_key=self._cache_key(disk_path),
            )
        return _cleanup(np.asarray(mask, dtype=bool))

    def warm(self, image: np.ndarray, disk_path=None) -> None:
        """Encode a slice ahead of the first click, on the viewer's
        slice-open warm.

        Encode only — no prompt, no decode, no mask to throw away. Warming a
        slice already in the LRU (the scrub-back case) costs nothing at all.
        """
        key = self._cache_key(disk_path)
        if key is None:
            # Nothing to file it under; warming would be discarded anyway.
            return
        # No `self._lock` here on purpose: `warm_slice` takes the GPU lock
        # without waiting, and holding an outer lock around it would rebuild
        # exactly the queue it exists to avoid.
        self._sam.warm_slice(np.asarray(image), cache_key=key)

    def is_warm(self, disk_path=None) -> bool:
        """Whether a click on this slice would skip the image encoder."""
        key = self._cache_key(disk_path)
        return key is not None and self._sam.is_slice_warm(key)

    def predict_mask_from_points(
        self, image: np.ndarray, points, point_labels, disk_path=None
    ) -> np.ndarray:
        return self._predict(
            image,
            points=[(float(x), float(y)) for x, y in points],
            point_labels=[int(v) for v in point_labels],
            disk_path=disk_path,
        )

    def predict_mask_from_box(self, image: np.ndarray, box_points, disk_path=None) -> np.ndarray:
        (x0, y0), (x1, y1) = box_points
        return self._predict(
            image,
            box=(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)),
            disk_path=disk_path,
        )


def _cleanup(mask: np.ndarray) -> np.ndarray:
    """The ~5% small-object cleanup the interactive tools have always applied
    (Cellable's own policy), so the model swap did not silently change what
    counts as speckle."""
    if not mask.any():
        return mask
    import skimage.morphology

    skimage.morphology.remove_small_objects(mask, max_size=mask.sum() * 0.05, out=mask)
    return mask
