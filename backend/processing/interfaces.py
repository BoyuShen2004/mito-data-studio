"""Processing-backend interface.

A backend executes a :class:`~processing.models.ProcessingJob`. It returns
plain result dicts; the service layer/dispatcher persists status transitions.
Backends must not run heavy work inside an HTTP request — the local backend
simulates or runs only lightweight safe commands, and the SLURM backend submits
to the cluster and polls.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class JobResult:
    """Outcome of a backend operation on a job."""

    status: str
    external_job_id: str = ""
    output_paths: dict = field(default_factory=dict)
    log_path: str = ""
    error_message: str = ""
    detail: str = ""


class ProcessingBackend(ABC):
    name: str = "base"

    @abstractmethod
    def submit(self, job) -> JobResult:
        """Submit ``job`` for execution; return its new status + external id."""

    @abstractmethod
    def poll(self, job) -> JobResult:
        """Poll an active ``job`` and return its current status."""

    @abstractmethod
    def cancel(self, job) -> JobResult:
        """Cancel ``job`` (best effort)."""

    def collect_outputs(self, job) -> dict:
        """Return/record output paths for a finished ``job`` (optional)."""
        return dict(job.output_paths or {})
