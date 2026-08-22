"""SAM2 tracking provider interface.

A tracking provider propagates seed masks through a volume. Providers only do
the *propagation* — model inference and nothing else. Everything that decides
identity is provider-independent and lives in the service layer, so every
backend shares it:

* :mod:`annotation.tracking.components` — connected-component splitting and
  cross-layer component→branch association;
* :mod:`annotation.tracking.contact` — contact detection and branch
  termination;
* :mod:`annotation.tracking.branching` — group bookkeeping and the auto-merge;
* :func:`annotation.tracking.services.run_branch_tracking` — the orchestration.

The heavy SAM2 model runs on a GPU HPC node, so the real adapter dispatches a
:class:`~processing.models.ProcessingJob` rather than loading a model inside the
web process. The ``local`` adapter is a dependency-free CPU stand-in used in
dev/tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass
class PropagationRequest:
    """Inputs for one propagation run over a z-range.

    ``seeds`` maps ``branch_id -> {z: 2D bool mask}``. Each branch is an
    independent SAM2 object id, already inferred upstream; the provider returns,
    per branch, the mask on every slice in ``z_range``.

    ``z_range`` is the user's **explicit, inclusive** ``(start_z, end_z)``
    propagation range. Providers must not extend past it. A provider is free to
    infer in whatever direction gives the best predictions inside that range
    (the SAM2 adapter propagates forward from the earliest seed and backward
    from the latest, then unions the two); merge/collision ordering is defined
    by ``start_z -> end_z`` regardless, in :mod:`annotation.tracking.contact`.
    """

    image: np.ndarray  # (Z, Y, X)
    seeds: dict[int, dict[int, np.ndarray]]
    z_range: tuple[int, int]


@dataclass
class PropagationResult:
    """Per-branch propagated masks: ``branch_id -> {z: 2D bool mask}``."""

    masks: dict[int, dict[int, np.ndarray]] = field(default_factory=dict)


class TrackingProvider(ABC):
    name: str = "base"
    #: Whether this provider needs a GPU node (drives processing-job dispatch).
    requires_gpu: bool = False

    @abstractmethod
    def propagate(self, request: PropagationRequest) -> PropagationResult:
        """Propagate each seeded branch across ``request.z_range``."""
