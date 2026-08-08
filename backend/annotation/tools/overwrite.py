"""Shared overwrite policies (Phase 9, P1).

Doc 19 ranks "Overwrite policies — empty-only vs everything" as a **tool-level**
P1, not an interpolation detail. Phase 8 implemented them inside
``interpolation/core.py`` because that was the first tool to need them; this is
their canonical home, and interpolation now imports from here.

Promoted rather than relocated: ``interpolation.core`` keeps its module-level
names as re-exports, so every existing reference and test still resolves.
"""

from __future__ import annotations

import numpy as np

OVERWRITE_ALL = "overwrite_all"
OVERWRITE_EMPTY = "overwrite_empty"
OVERWRITE_MODES = frozenset({OVERWRITE_ALL, OVERWRITE_EMPTY})

#: The conservative default. Silently destroying an existing segment is the
#: worse failure, so a tool must opt in to overwriting.
DEFAULT_OVERWRITE_MODE = OVERWRITE_EMPTY


def is_valid_mode(mode: str) -> bool:
    return mode in OVERWRITE_MODES


def writable_mask(labels: np.ndarray, mask: np.ndarray, *,
                  overwrite_mode: str) -> np.ndarray:
    """Narrow ``mask`` to the voxels the policy actually permits writing.

    Returned rather than applied, so a caller can count or preview the affected
    voxels without mutating anything — which is what makes the plan step of the
    plan/apply pattern honest.
    """
    if overwrite_mode == OVERWRITE_ALL:
        return mask
    # OVERWRITE_EMPTY: existing segments win.
    return mask & (labels == 0)
