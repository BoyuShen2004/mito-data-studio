"""Basic file-level submission QC.

This is the modular home of the original ``run_basic_qc`` checks: the label file
must be linked to a task, exist on storage, be non-empty, and carry an allowed
extension. Behaviour is preserved; the result is now returned in the standard
provider shape (``passed``/``checks``/``metrics``/``warnings``/``errors``).

An **in-app** submission (``submission.source == SubmissionSource.INAPP``,
see ``annotation.services.submit_inapp_annotation``) has no uploaded
``label_file`` at all — there's nothing to upload, the content already lives
server-side as the volume's working label copy
(``annotation.label_paths.working_label_rel_path``). The checks below run
against *that* file instead in this case; the extension check is skipped
(it's always this app's own ``.tif``, never user-chosen).
"""

from __future__ import annotations

import os

from django.conf import settings

from ..interfaces import QualityControlProvider, add_check, empty_report


class BasicQualityControlProvider(QualityControlProvider):
    name = "basic"

    def validate_submission(self, submission) -> dict:
        report = empty_report()

        add_check(
            report,
            "linked_to_task",
            submission.task_id is not None,
            "Submission is not linked to a task",
        )

        if submission.label_file:
            self._check_uploaded_file(report, submission)
        else:
            self._check_working_copy(report, submission)

        report["metrics"].update(self.calculate_metrics(submission))
        return report

    def _check_uploaded_file(self, report: dict, submission) -> None:
        field = submission.label_file
        exists = bool(field) and field.storage.exists(field.name)
        add_check(report, "file_exists", exists, "Label file does not exist on storage")

        size = 0
        if exists:
            try:
                size = field.size
            except (OSError, ValueError):
                size = 0
        add_check(report, "non_empty", size > 0, "Label file is empty")

        name = field.name if field else ""
        ext = matched_label_extension(name)
        add_check(
            report,
            "allowed_extension",
            bool(ext),
            f"Extension not allowed for '{os.path.basename(name)}'",
        )
        report["metrics"]["file_size"] = size
        report["metrics"]["extension"] = ext
        # Backwards-compatible top-level keys some callers/tests may read.
        report["file_size"] = size
        report["extension"] = ext

    def _check_working_copy(self, report: dict, submission) -> None:
        from ...label_paths import working_label_rel_path
        from ...visualization.slice_io import resolve_path

        volume = submission.task.volume
        path = resolve_path(working_label_rel_path(volume))
        exists = path.exists()
        add_check(
            report, "file_exists", exists,
            "The in-app working label copy does not exist — nothing was annotated",
        )

        size = path.stat().st_size if exists else 0
        add_check(report, "non_empty", size > 0, "Working label copy is empty")
        # No extension check: this is always this app's own owned .tif, never
        # user-chosen, so there's nothing meaningful to validate there.

        report["metrics"]["file_size"] = size
        report["metrics"]["extension"] = path.suffix
        report["file_size"] = size
        report["extension"] = path.suffix

    def calculate_metrics(self, submission) -> dict:
        if submission.label_file:
            return {"filename": os.path.basename(submission.label_file.name)}
        from ...label_paths import working_label_rel_path

        return {"filename": working_label_rel_path(submission.task.volume)}


def matched_label_extension(name: str) -> str:
    """Return the allowed label extension ``name`` ends with, or ``""``.

    Longest-match first so ``.nii.gz`` beats ``.gz``.
    """
    lower = name.lower()
    for ext in sorted(settings.MITO_ALLOWED_LABEL_EXTENSIONS, key=len, reverse=True):
        if lower.endswith(ext):
            return ext
    return ""
