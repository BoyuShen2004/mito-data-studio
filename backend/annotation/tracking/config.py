"""Central, tunable thresholds for provider-independent tracking logic.

Everything the automatic branch inference and the child touch/merge lifecycle
decide is driven by the numbers below. They live in one module (rather than as
literals scattered through :mod:`annotation.tracking.components` and
:mod:`annotation.tracking.contact`) so they are documented once, testable by
patching one place, and adjustable per deployment through Django settings
without editing code.

Every value may be overridden with the matching ``MITO_TRACK_*`` setting.
``settings`` is read on each call rather than captured at import time, so
``override_settings`` in tests behaves the way test authors expect.

Units are **pixels on one z slice** unless stated otherwise.
"""

from __future__ import annotations

from django.conf import settings

#: Connected components with fewer than this many pixels are dropped before a
#: seed mask becomes a branch. Deliberately tiny: a real mitochondrion cross
#: section at the magnifications this tool is used at is hundreds of pixels,
#: while the strays this removes are the 1-3 px crumbs a brush leaves behind
#: when the annotator clips a stroke. Raising it risks silently deleting a
#: legitimate small mitochondrion, so the default stays conservative.
MIN_COMPONENT_AREA = 4

#: A component overlapping a branch at all is always a credible match. When
#: there is no overlap, the centroids must be within this multiple of the two
#: components' combined equivalent radii (``sqrt(area / pi)``). Scale-aware, so
#: it behaves the same for small and large mitochondria.
MATCH_DISTANCE_FACTOR = 2.0

#: When the best and second-best branch for one component score within this IoU
#: margin of each other, the association is reported as ambiguous. The
#: assignment is still made deterministically; the caller gets a warning so the
#: Track preview can say so instead of silently swapping identities.
AMBIGUOUS_IOU_MARGIN = 0.05

#: Contact strength (see :func:`annotation.tracking.contact.contact_strength`)
#: at or above this on a *single* layer is a meaningful merge on its own.
STRONG_CONTACT_PIXELS = 8

#: Below ``STRONG_CONTACT_PIXELS`` a merge needs to be sustained. This is the
#: floor a sustained run must still reach on one of its layers, so a run of
#: single stray edge pixels never terminates a branch.
MIN_CONTACT_PIXELS = 2

#: How many consecutive layers a weak contact must persist before it counts.
CONTACT_SUSTAIN_LAYERS = 2

#: How many recent pre-contact layers the survivor's rolling median area is
#: taken over. Using a window rather than the contact layer alone keeps one
#: noisy slice from deciding which branch dies.
AREA_WINDOW_LAYERS = 3


def _setting(name: str, default):
    value = getattr(settings, name, None)
    return default if value is None else value


def min_component_area() -> int:
    return max(1, int(_setting("MITO_TRACK_MIN_COMPONENT_AREA", MIN_COMPONENT_AREA)))


def match_distance_factor() -> float:
    return float(_setting("MITO_TRACK_MATCH_DISTANCE_FACTOR", MATCH_DISTANCE_FACTOR))


def ambiguous_iou_margin() -> float:
    return float(_setting("MITO_TRACK_AMBIGUOUS_IOU_MARGIN", AMBIGUOUS_IOU_MARGIN))


def strong_contact_pixels() -> int:
    return max(1, int(_setting("MITO_TRACK_STRONG_CONTACT_PIXELS", STRONG_CONTACT_PIXELS)))


def min_contact_pixels() -> int:
    return max(1, int(_setting("MITO_TRACK_MIN_CONTACT_PIXELS", MIN_CONTACT_PIXELS)))


def contact_sustain_layers() -> int:
    return max(1, int(_setting("MITO_TRACK_CONTACT_SUSTAIN_LAYERS", CONTACT_SUSTAIN_LAYERS)))


def area_window_layers() -> int:
    return max(1, int(_setting("MITO_TRACK_AREA_WINDOW_LAYERS", AREA_WINDOW_LAYERS)))
