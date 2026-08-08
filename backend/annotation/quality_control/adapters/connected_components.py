"""Connected-component (scientific) QC — future extension placeholder.

Voxel-level scientific acceptance (component counts, split/merge detection,
shape-vs-source matching) is intentionally out of the MVP scope. This adapter
runs the basic file checks and records that scientific QA is not yet configured,
so selecting it never blocks a submission. Fill in ``calculate_metrics`` with a
real analysis (e.g. via ``tifffile`` + ``scipy.ndimage.label``) to enable it.
"""

from __future__ import annotations

from .basic import BasicQualityControlProvider


class ConnectedComponentsQualityControlProvider(BasicQualityControlProvider):
    name = "connected_components"

    def validate_submission(self, submission) -> dict:
        report = super().validate_submission(submission)
        report["warnings"].append(
            "Connected-component scientific QA is not configured; "
            "only basic file checks were run."
        )
        report["metrics"]["scientific_qc"] = "not_configured"
        return report
