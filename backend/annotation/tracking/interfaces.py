"""SAM2 tracking provider interface.

A tracking provider propagates seed masks through a volume. Providers only do
the *propagation*; the fork-aware branch bookkeeping and auto-merge live in the
service layer (:mod:`annotation.tracking.branching` +
:func:`annotation.services.run_branch_tracking`) so every backend shares it.

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
    independent SAM2 object id (a fork branch); the provider returns, per branch,
    the mask on every slice in ``z_range``.
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
