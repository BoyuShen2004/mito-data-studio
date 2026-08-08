"""Quality-control provider interface.

A QC provider inspects an :class:`~annotation.models.AnnotationSubmission` and
returns a structured result. It does **not** persist anything — the service
layer maps the result to a :class:`~core.choices.QCStatus` and saves it, keeping
persistence in one place.

Result contract (``validate_submission``)::

    {
        "passed": bool,          # False if any error-level check failed
        "checks": [              # ordered, one per check performed
            {"name": str, "ok": bool, "level": "errors" | "warnings"},
        ],
        "metrics": {...},        # provider-specific measurements
        "warnings": [str, ...],  # human-readable warning messages
        "errors": [str, ...],    # human-readable error messages
    }
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class QualityControlProvider(ABC):
    """Base class for submission quality-control providers."""

    #: Registry key / human name for this provider.
    name: str = "base"

    @abstractmethod
    def validate_submission(self, submission) -> dict:
        """Run checks on ``submission`` and return the structured result."""

    def calculate_metrics(self, submission) -> dict:
        """Return provider-specific measurements (optional; default empty)."""
        return {}


def empty_report() -> dict:
    """A zero-check report skeleton in the standard shape."""
    return {"passed": True, "checks": [], "metrics": {}, "warnings": [], "errors": []}


def add_check(report: dict, name: str, ok: bool, message: str = "", level: str = "errors") -> None:
    """Append a check to ``report`` and record its message when it fails."""
    report["checks"].append({"name": name, "ok": bool(ok), "level": level})
    if not ok:
        report.setdefault(level, []).append(message or name)
        if level == "errors":
            report["passed"] = False
