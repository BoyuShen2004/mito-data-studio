"""Image preprocessing for the interactive AI-mask tools.

Ported from ``cellable/labelme/app.py``'s ``normalizeImg`` — this is the
exact function Cellable feeds the EfficientSAM encoder (via
``_setCurrentImageFromSlice``), and it is deliberately **different** from
``annotation/visualization/slice_io.py``'s ``display_range``:

- ``display_range`` computes one lo/hi **per volume** (sampled percentiles
  over a few whole slices, or the dtype range for uint8), reused for every
  slice so brightness stays stable while a human scrubs through the stack —
  the right behavior for a *display* image.
- ``normalizeImg`` computes lo/hi **per slice**, from that slice's **non-
  zero** pixels only (1st/99.5th percentile) — sparse EM slices have a
  large zero-valued background that would otherwise dominate a plain
  min/max or whole-slice percentile stretch, washing out the foreground the
  model actually needs to see.

Per progress/history/21-cellable-parity-followups.md: mito's Point Mask /
Box Mask / Boundary tools were feeding the encoder an image normalized the
*display* way, not the way Cellable actually feeds its own model — a real
source of mask divergence independent of which EfficientSAM weight tier is
loaded. This module exists so the AI-mask endpoints can match Cellable's
input pixel-for-pixel (same model, same prompt, same preprocessing -> same
mask, modulo float rounding), while the JPEG/PNG slice-streaming endpoints
keep using ``display_range`` for what it's actually good at (stable
brightness while scrubbing).
"""

from __future__ import annotations

import numpy as np


def normalize_for_ai(img: np.ndarray) -> np.ndarray:
    """Stretch one 2D intensity slice to uint8 the way Cellable's
    ``normalizeImg`` does, for feeding to the ported EfficientSAM model."""
    arr = np.asarray(img)
    if arr.size == 0:
        return np.zeros_like(arr, dtype=np.uint8)

    arr = arr.astype(np.float32, copy=False)
    nonzero = arr[arr > 0]

    if nonzero.size > 0:
        # Both cut points in one call. `np.percentile` partitions its input
        # around the requested quantiles, so asking twice partitioned the
        # non-zero copy twice for no reason — on a 1024x1024 crop that was
        # 28.9ms -> 22.3ms per normalize, on the two busiest AI endpoints
        # (`warm-embedding` and `predict-mask`), for a bit-identical result.
        low, high = (float(value) for value in np.percentile(nonzero, (1.0, 99.5)))
    else:
        low = float(np.min(arr))
        high = float(np.max(arr))

    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        if nonzero.size == 0:
            return np.zeros_like(arr, dtype=np.uint8)
        return (arr > 0).astype(np.uint8) * 255

    arr = np.clip(arr, low, high)
    arr = 255.0 * (arr - low) / (high - low)
    return arr.astype(np.uint8)
