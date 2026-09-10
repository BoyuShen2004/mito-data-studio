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

Choosing between SAM 2's three answers is the other half of the job, and it
is not the model's to make. See `_choose_point_mask`.
"""

from __future__ import annotations

from pathlib import Path
import threading

import numpy as np

# --- What counts as a plausible answer to one click -------------------------
#
# A single point makes SAM 2 emit three masks — roughly "subpart", "part" and
# "whole" — ranked by the model's own predicted IoU. The ranking is good at
# ordering sensible masks and has no way to notice when none of them is: one
# of the three is routinely a blanket over the entire field, ragged and pitted
# along every membrane it crosses, and nothing in the score says so. Over 363
# evenly spaced clicks per volume across three slices, that blanket (>50% of
# the plane) was the top-ranked answer on 49/363 podo clicks, 8/363 stem,
# 87/363 beta and 136/363 jurkat; counting shredded and hairline answers too,
# 17.4% of podo clicks came back unusable against 4.1% of stem.
#
# Clicks squarely inside an annotated object rarely hit this on any volume,
# which is why it reads as a podo problem: podo's mitochondria touch and fill
# ~71% of the plane, so an ordinary click is far more often the near-boundary
# kind where the blanket wins. It is the same defect everywhere, hit at very
# different rates.
#
# So a candidate has to look like one organelle before its predicted IoU is
# allowed to win. The largest real cross-section in any of the four me2-*
# volumes is 9.6% of its plane (podo; stem 5.4%, beta 6.4%, jurkat 6.5%), and
# the thinnest 1% of real objects still measure 7.2 px across, so these bounds
# reject blankets and ribbons without touching anything real — over 190
# ground-truth object clicks the whole filter turned away exactly one, and
# mean IoU against the annotation moved 0.730 -> 0.733 on podo and not at all
# on the other three.
#
# 0.15 is where it stops mattering: sweeping the bound over those same clicks,
# every answer between "one organelle" and "the whole field" disappears at
# 0.15 and comes back above it (podo keeps 18 field-sized answers at 0.20, 29
# at 0.35), while the cost stays one refused ground-truth click in 190.
#
# The fraction is a setting because it is the one bound that depends on the
# data: a volume whose objects genuinely fill a fifth of the plane needs it
# raised, and would otherwise see every click refused.
_MAX_PLANE_FRACTION_DEFAULT = 0.15
# If every candidate misses that strict organelle bound, a click-anchored mask
# may still be useful enough to refine. On podo z=1, 0.25 recovered every
# sampled strict miss (the useful candidates were 15.0–20.4%) while the bad
# alternatives covered 100% of the plane.
_FALLBACK_MAX_PLANE_FRACTION_DEFAULT = 0.25
# The final "smallest anchored answer" rung is deliberately independent of
# the tunable fallback ceiling, but it must never resurrect a field-sized
# shred. Half a plane is already a very generous single-object preview.
_HARD_MAX_PLANE_FRACTION = 0.50
# "Covers the frame" is a claim about a field of view, and a frame this small
# has no field of view to speak of — one object can honestly fill a quarter of
# it. Annotation planes here are 256px and up; below this the size bound is
# not evidence of anything, so only the shape bounds below apply.
_MIN_FRAME_PX = 128 * 128
_MIN_THICKNESS_PX = 4.0  # 2x the largest inscribed radius — rejects ribbons
_MIN_AREA_PX = 32

# Small annotation planes (e.g. me2-* at 256²) are a different operating
# point from the 1k–2k crops Track usually sees: the same mitochondrion is
# fewer pixels, fit-window magnifies every defect, and SAM 2's internal
# resize from 256→1024 is a large bilinear stretch of already-aliased EM.
# Running the image predictor on an explicitly upscaled plane (then scaling
# the mask back with a soft threshold) makes small and large volumes share a
# more similar native resolution at the model, which is what "volume size
# should not decide quality" means in practice. Tiny fixtures (<128) are left
# alone so unit tests keep their coordinate arithmetic.
_UPSCALE_MIN_SIDE = 128
_UPSCALE_TARGET_SIDE = 1024


def _max_plane_fraction() -> float:
    try:
        from django.conf import settings

        return float(getattr(settings, "MITO_AI_MASK_MAX_PLANE_FRACTION",
                             _MAX_PLANE_FRACTION_DEFAULT))
    except Exception:
        return _MAX_PLANE_FRACTION_DEFAULT


def _fallback_max_plane_fraction() -> float:
    try:
        from django.conf import settings

        configured = float(
            getattr(
                settings,
                "MITO_AI_MASK_FALLBACK_MAX_PLANE_FRACTION",
                _FALLBACK_MAX_PLANE_FRACTION_DEFAULT,
            )
        )
    except Exception:
        configured = _FALLBACK_MAX_PLANE_FRACTION_DEFAULT
    # A fallback cannot be stricter than the primary gate, and even a bad
    # deployment override must not make near-full-plane masks eligible.
    return min(_HARD_MAX_PLANE_FRACTION, max(_max_plane_fraction(), configured))


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
    def _cache_key(disk_path, native_hw=None) -> str | None:
        """`disk_path` already identifies (volume, axis, index, variant, mtime).

        When the native plane is small enough to upscale, the key is tagged so
        features computed on the upscaled RGB are never served for a
        pre-upscale encode (or the reverse).
        """
        if not disk_path:
            return None
        key = str(disk_path)
        if native_hw is not None:
            side = max(int(native_hw[0]), int(native_hw[1]))
            if _UPSCALE_MIN_SIDE <= side < _UPSCALE_TARGET_SIDE:
                key = f"{key}#up{_UPSCALE_TARGET_SIDE}"
        return key

    def _predict(self, image, *, points=None, point_labels=None, box=None, disk_path=None):
        image = np.asarray(image)
        native = image.shape[:2]
        key = self._cache_key(disk_path, native)
        image, points, box, _scale = _maybe_upscale(image, points=points, box=box)
        with self._lock:
            mask = self._sam.predict_single_frame(
                image,
                points=points,
                point_labels=point_labels,
                box=box,
                cache_key=key,
            )
        mask = _downscale_mask(np.asarray(mask, dtype=bool), native)
        return _cleanup(mask)

    def warm(self, image: np.ndarray, disk_path=None) -> None:
        """Encode a slice ahead of the first click, on the viewer's
        slice-open warm.

        Encode only — no prompt, no decode, no mask to throw away. Warming a
        slice already in the LRU (the scrub-back case) costs nothing at all.
        Uses the same upscale as predict so the warm cache matches the click.
        """
        image = np.asarray(image)
        key = self._cache_key(disk_path, image.shape[:2])
        if key is None:
            # Nothing to file it under; warming would be discarded anyway.
            return
        image, _, _, _ = _maybe_upscale(image)
        # No `self._lock` here on purpose: `warm_slice` takes the GPU lock
        # without waiting, and holding an outer lock around it would rebuild
        # exactly the queue it exists to avoid.
        self._sam.warm_slice(image, cache_key=key)

    def is_warm(self, disk_path=None, *, native_hw=None) -> bool:
        """Whether a click on this slice would skip the image encoder."""
        key = self._cache_key(disk_path, native_hw)
        return key is not None and self._sam.is_slice_warm(key)

    def predict_mask_from_points(
        self, image: np.ndarray, points, point_labels, disk_path=None
    ) -> np.ndarray:
        image = np.asarray(image)
        native = image.shape[:2]
        prompts = [(float(x), float(y)) for x, y in points]
        labels = [int(v) for v in point_labels]
        key = self._cache_key(disk_path, native)
        image, prompts_up, _, _scale = _maybe_upscale(image, points=prompts)
        with self._lock:
            masks, ious = self._sam.predict_single_frame(
                image,
                points=prompts_up,
                point_labels=labels,
                cache_key=key,
                candidates=True,
            )
        masks = [_downscale_mask(np.asarray(m, dtype=bool), native) for m in masks]
        # Plausibility / click-anchor use native coordinates.
        positives = [p for p, label in zip(prompts, labels) if label == 1]
        return _choose_point_mask(masks, ious, positives, native)

    def predict_mask_from_box(self, image: np.ndarray, box_points, disk_path=None) -> np.ndarray:
        (x0, y0), (x1, y1) = box_points
        return self._predict(
            image,
            box=(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)),
            disk_path=disk_path,
        )


def _maybe_upscale(image, points=None, box=None):
    """Upscale small planes so SAM 2 sees a large-volume-like resolution."""
    image = np.asarray(image)
    h, w = image.shape[:2]
    side = max(h, w)
    if side < _UPSCALE_MIN_SIDE or side >= _UPSCALE_TARGET_SIDE:
        return image, points, box, 1.0
    scale = _UPSCALE_TARGET_SIDE / float(side)
    nh = max(1, int(round(h * scale)))
    nw = max(1, int(round(w * scale)))
    from PIL import Image as PILImage

    up = np.asarray(
        PILImage.fromarray(image).resize((nw, nh), PILImage.BILINEAR)
    )
    pts = None
    if points is not None:
        pts = [(float(x) * scale, float(y) * scale) for x, y in points]
    box2 = None
    if box is not None:
        x0, y0, x1, y1 = box
        box2 = (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
    return up, pts, box2, scale


def _downscale_mask(mask: np.ndarray, out_hw: tuple[int, int]) -> np.ndarray:
    """Scale a mask back to the native plane.

    Bilinear + mid threshold (not nearest) fills single-pixel gaps that the
    upscale/decode round trip would otherwise leave as horizontal pinholes —
    the thing that reads as scanlines on cristae-heavy EM.
    """
    mask = np.asarray(mask, dtype=bool)
    h, w = out_hw
    if mask.shape == (h, w):
        return mask
    from PIL import Image as PILImage

    arr = PILImage.fromarray(mask.astype(np.uint8) * 255).resize(
        (w, h), PILImage.BILINEAR
    )
    return np.asarray(arr) > 127


def _choose_point_mask(masks, ious, positives, shape) -> np.ndarray:
    """Choose the best useful answer to *these clicks* via a fallback ladder.

    Three things happen here, in order, and each one has to run before the
    next is meaningful:

    1. **Anchor on the click.** A prompt answers the object under the point,
       so components that hold no positive click are somebody else's object.
       Dropping them is what `_cleanup`'s size threshold cannot do: a stray
       lobe the same size as the real one is not speckle, it is a different
       mitochondrion, and only the click says which is which.
    2. Prefer strict organelle-shaped candidates, ranked by predicted IoU.
    3. If strict rejects everything, allow the best-IoU anchored candidate up
       to the relaxed plane fraction. This is the normal dense-EM recovery.
    4. Finally, choose the smallest anchored, thick-enough candidate below a
       hard half-plane guard. This favours a refinable component over a hard
       stop without ever reviving a near-full-frame blanket.

    Every rung requires *all* positive clicks. Empty is reserved for the case
    where SAM 2 truly produced no anchored, object-shaped answer.
    """
    plane = int(shape[0]) * int(shape[1])
    bounded = plane >= _MIN_FRAME_PX
    strict_ceiling = _max_plane_fraction() * plane if bounded else float("inf")
    relaxed_ceiling = (
        _fallback_max_plane_fraction() * plane if bounded else float("inf")
    )
    hard_ceiling = _HARD_MAX_PLANE_FRACTION * plane if bounded else float("inf")
    candidates = []
    for mask, iou in zip(masks, ious):
        mask = _anchor_to_clicks(_cleanup(np.array(mask, dtype=bool)), positives)
        candidates.append((mask, float(iou)))

    strict = [
        (mask, iou)
        for mask, iou in candidates
        if _is_plausible(mask, positives, strict_ceiling)
    ]
    if strict:
        return max(strict, key=lambda item: item[1])[0]

    relaxed = [
        (mask, iou)
        for mask, iou in candidates
        if _is_plausible(mask, positives, relaxed_ceiling)
    ]
    if relaxed:
        return max(relaxed, key=lambda item: item[1])[0]

    last_resort = [
        (mask, iou)
        for mask, iou in candidates
        if _is_plausible(mask, positives, hard_ceiling)
    ]
    if last_resort:
        return min(last_resort, key=lambda item: (int(item[0].sum()), -item[1]))[0]

    return np.zeros(shape, dtype=bool)


def _anchor_to_clicks(mask: np.ndarray, positives) -> np.ndarray:
    """Keep only the connected components a positive click lands in.

    8-connected, matching how the rest of the app splits masks into objects
    (`annotation.tracking.components`). A mask holding no click at all is
    returned untouched — there is nothing to anchor to, and `_is_plausible`
    is the one that decides such a mask is unusable.
    """
    if not positives or not mask.any():
        return mask
    import scipy.ndimage as ndi

    labelled, count = ndi.label(mask, structure=np.ones((3, 3), dtype=bool))
    if count <= 1:
        return mask
    keep = {int(labelled[int(y), int(x)]) for x, y in positives}
    keep.discard(0)
    if not keep:
        return mask
    return np.isin(labelled, sorted(keep))


def _is_plausible(mask: np.ndarray, positives, ceiling: float) -> bool:
    """Whether this mask could be one organelle around these clicks."""
    area = int(mask.sum())
    if area < _MIN_AREA_PX or area > ceiling:
        return False
    if not all(mask[int(y), int(x)] for x, y in positives):
        return False
    import scipy.ndimage as ndi

    # 2x the largest inscribed radius: the width of the mask at its widest,
    # which is what separates a lobe from a one-pixel ribbon along a membrane.
    return float(ndi.distance_transform_edt(mask).max()) * 2.0 >= _MIN_THICKNESS_PX


def _cleanup(mask: np.ndarray) -> np.ndarray:
    """The ~5% small-object cleanup the interactive tools have always applied
    (Cellable's own policy), so the model swap did not silently change what
    counts as speckle — now applied to holes on the same terms.

    A pinhole is the same artifact as a fleck, seen from the inside, and it
    costs more: the preview traces every boundary edge it owns, so a mask
    pitted with them reads as texture rather than as an object. Filling them
    moves a median of 0.0% and a mean of 0.7% of a real mask's area.

    On small planes a one-pixel morphological close then seals any remaining
    single-row gaps the upscale/downscale round-trip can leave — the thing
    that reads as scanlines under fit-window magnification.
    """
    if not mask.any():
        return mask
    import skimage.morphology

    limit = mask.sum() * 0.05
    skimage.morphology.remove_small_objects(mask, max_size=limit, out=mask)
    if not mask.any():
        return mask
    mask = skimage.morphology.remove_small_holes(mask, max_size=limit)
    h, w = mask.shape
    if max(h, w) < _UPSCALE_TARGET_SIDE and mask.any():
        mask = skimage.morphology.closing(mask, skimage.morphology.disk(1))
    return mask
