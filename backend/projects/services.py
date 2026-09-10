"""Deterministic service functions for projects.

These are the stable building blocks reused by views, admin actions,
management commands, and (later) agent tools. They take plain arguments and
return model instances or plain dicts.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from core.choices import ANNOTATION_TYPE_TO_WORKFLOW, TaskStatus, WorkflowType

from .models import Dataset, Project

logger = logging.getLogger(__name__)


def ensure_project_folder(project: Project) -> None:
    """Create ``project``'s folder under ``MITO_DATA_ROOT`` immediately, even
    though it stays empty until a volume is annotated (see
    ``annotation.label_paths`` — the working label copy itself is only ever
    written when an annotator starts painting/tracking). Lets the on-disk
    layout mirror the project → dataset → volume hierarchy from the moment a
    project is registered, not just once someone starts working on it.

    Imports deferred (rather than module-level) to avoid a load-order
    dependency between the ``projects`` and ``annotation`` apps.
    """
    from annotation.label_paths import project_folder_rel_path
    from annotation.visualization.slice_io import resolve_path

    resolve_path(project_folder_rel_path(project)).mkdir(parents=True, exist_ok=True)


def ensure_dataset_folder(project: Project, dataset: Dataset) -> None:
    """Same as :func:`ensure_project_folder`, one level down."""
    from annotation.label_paths import dataset_folder_rel_path
    from annotation.visualization.slice_io import resolve_path

    resolve_path(dataset_folder_rel_path(project, dataset)).mkdir(
        parents=True, exist_ok=True
    )


class DeleteBlocked(Exception):
    """Raised when deleting something would destroy existing annotation work.

    Carries the dependent counts so callers can tell the user exactly what is
    in the way instead of failing opaquely.
    """

    def __init__(self, message: str, counts: dict):
        super().__init__(message)
        self.counts = counts


def create_project(
    *,
    title: str,
    created_by=None,
    institution=None,
    description: str = "",
    annotation_target: str = "mitochondria",
    annotation_type: str | None = None,
    workflow_type: str | None = None,
    deadline=None,
    status: str | None = None,
    dataset: str = "",
    metadata: dict | None = None,
    reviewed: bool = False,
) -> Project:
    """Create and return a new :class:`Project`.

    ``reviewed`` marks the project as manager-reviewed on creation (used when a
    manager registers data directly); requester-registered data stays pending.
    ``workflow_type`` defaults to the value derived from ``annotation_type``
    (see :data:`core.choices.ANNOTATION_TYPE_TO_WORKFLOW`).
    """
    kwargs = {
        "title": title,
        "created_by": created_by,
        "institution": institution,
        "description": description,
        "annotation_target": annotation_target,
        "dataset": dataset or "",
        "metadata": metadata or {},
        "manager_reviewed": reviewed,
        "workflow_type": resolve_workflow_type(workflow_type, annotation_type),
    }
    if reviewed:
        kwargs["reviewed_by"] = created_by
        kwargs["reviewed_at"] = timezone.now()
    if annotation_type is not None:
        kwargs["annotation_type"] = annotation_type
    if status is not None:
        kwargs["status"] = status
    if deadline is not None:
        kwargs["deadline"] = deadline
    project = Project.objects.create(**kwargs)
    ensure_project_folder(project)
    return project


def resolve_workflow_type(
    workflow_type: str | None, annotation_type: str | None = None
) -> str:
    """Resolve the workflow type, deriving it from ``annotation_type`` if unset.

    Explicit ``workflow_type`` wins. Otherwise it is inferred from the (older,
    more specific) ``annotation_type``; failing that it defaults to annotation.
    """
    if workflow_type:
        return workflow_type
    if annotation_type:
        return ANNOTATION_TYPE_TO_WORKFLOW.get(
            annotation_type, WorkflowType.ANNOTATION
        )
    return WorkflowType.ANNOTATION


def mark_project_reviewed(project: Project, reviewer=None, reviewed: bool = True) -> Project:
    """Approve (or un-approve) a project so its volumes can be split/assigned.

    Central review-state transition reused by the DRF endpoint and the admin
    action, keeping the reviewer/timestamp bookkeeping in one place.
    """
    project.manager_reviewed = bool(reviewed)
    project.reviewed_by = reviewer if reviewed else None
    project.reviewed_at = timezone.now() if reviewed else None
    project.save(update_fields=["manager_reviewed", "reviewed_by", "reviewed_at"])
    return project


def calculate_project_progress(project: Project) -> dict:
    """Return counts and a completion percentage for a project's tasks.

    Phase 6: the status tally is a grouped database query. It used to be

        for task in tasks.only("status"):
            status_counts[task.status] = ... + 1

    which transferred and instantiated **every task row** to produce six
    integers — the cost grew with the project while the answer stayed the same
    size. Output is unchanged; ``test_matches_legacy_python_tally`` asserts that
    against an explicit reimplementation of the old loop.

    Not behind ``FEATURE_DASHBOARDS`` on purpose: this changes how a number is
    computed, not what it is, and hiding a performance fix behind an opt-in flag
    would ship the slow path to everyone who has not opted in.
    """
    from core.statistics import task_status_counts

    status_counts = task_status_counts(project)
    total = sum(status_counts.values())
    approved = status_counts.get(TaskStatus.APPROVED, 0)
    percent = round(100 * approved / total, 1) if total else 0.0

    return {
        "total_tasks": total,
        "approved_tasks": approved,
        "percent_complete": percent,
        "status_counts": status_counts,
        "volumes": project.volumes.count(),
    }


# --- Datasets --------------------------------------------------------------


def get_or_create_dataset(
    *,
    project: Project,
    name: str,
    description: str = "",
    metadata: dict | None = None,
    image_directory: str = "",
    region_mask_directory: str = "",
    mask_directory: str = "",
) -> Dataset:
    """Fetch (or create) a dataset by name within ``project``.

    Registering more data under an existing dataset name adds to it rather than
    creating a duplicate; supplied metadata is merged into what is already there.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("A dataset name is required.")

    dataset, created = Dataset.objects.get_or_create(
        project=project,
        name=name,
        defaults={
            "description": description or "",
            "metadata": metadata or {},
            "image_directory": image_directory or "",
            "region_mask_directory": region_mask_directory or "",
            "mask_directory": mask_directory or "",
        },
    )
    if not created:
        changed = []
        if metadata:
            dataset.metadata = {**(dataset.metadata or {}), **metadata}
            changed.append("metadata")
        if description and not dataset.description:
            dataset.description = description
            changed.append("description")
        # Remember the most recent source directories.
        for field, value in (
            ("image_directory", image_directory),
            ("region_mask_directory", region_mask_directory),
            ("mask_directory", mask_directory),
        ):
            if value and getattr(dataset, field) != value:
                setattr(dataset, field, value)
                changed.append(field)
        if changed:
            dataset.save(update_fields=changed)

    # Keep the project's legacy single-dataset name pointing at its first one.
    if not (project.dataset or "").strip():
        project.dataset = name
        project.save(update_fields=["dataset"])
    ensure_dataset_folder(project, dataset)
    return dataset


def update_dataset(dataset: Dataset, **fields) -> Dataset:
    """Update a dataset's editable fields. Metadata merges; the rest replace."""
    allowed = {
        "name",
        "description",
        "image_directory",
        "region_mask_directory",
        "mask_directory",
        "project",
    }
    changed = []
    for key, value in fields.items():
        if key == "metadata":
            # Passing an explicit null for a key removes it, so corrections stick.
            merged = {**(dataset.metadata or {}), **(value or {})}
            dataset.metadata = {k: v for k, v in merged.items() if v is not None}
            changed.append("metadata")
        elif key in allowed and value is not None:
            setattr(dataset, key, value)
            changed.append(key)
    if changed:
        dataset.save(update_fields=changed)
        if "project" in changed:
            # Volumes follow their dataset so the denormalised FK stays true.
            dataset.volumes.update(project=dataset.project)
    return dataset


# --- Deletion guards -------------------------------------------------------
#
# Deleting a project or dataset cascades to volumes, tasks and submissions.
# Annotator output is expensive to recreate, so deletion is refused while any
# exists unless the caller explicitly forces it.


def _work_counts(*, projects=None, datasets=None, volumes=None) -> dict:
    """Count the volumes/tasks/submissions/reviews hanging off a selection."""
    from annotation.models import AnnotationSubmission, AnnotationTask, ReviewRecord
    from volumes.models import Volume

    volume_qs = Volume.objects.none()
    if projects is not None:
        volume_qs = Volume.objects.filter(project__in=projects)
    elif datasets is not None:
        volume_qs = Volume.objects.filter(dataset__in=datasets)
    elif volumes is not None:
        volume_qs = volumes

    tasks = AnnotationTask.objects.filter(volume__in=volume_qs)
    submissions = AnnotationSubmission.objects.filter(task__in=tasks)
    return {
        "volumes": volume_qs.count(),
        "tasks": tasks.count(),
        "submissions": submissions.count(),
        "reviews": ReviewRecord.objects.filter(submission__in=submissions).count(),
    }


def describe_project_dependents(project: Project) -> dict:
    counts = _work_counts(projects=[project])
    counts["datasets"] = project.datasets.count()
    return counts


def describe_dataset_dependents(dataset: Dataset) -> dict:
    return _work_counts(datasets=[dataset])


def describe_volume_dependents(volume) -> dict:
    from volumes.models import Volume

    return _work_counts(volumes=Volume.objects.filter(pk=volume.pk))


def _guard(label: str, counts: dict, force: bool) -> None:
    """Refuse a delete that would throw away annotator work, unless forced."""
    if force:
        return
    blocking = counts.get("tasks", 0) or counts.get("submissions", 0)
    if not blocking:
        return
    detail = (
        f"{counts['volumes']} volume(s), {counts['tasks']} task(s) and "
        f"{counts['submissions']} submission(s)"
    )
    raise DeleteBlocked(
        f"Cannot delete {label}: it still has {detail}. Delete the work first, "
        f"or confirm deleting it anyway.",
        counts,
    )


# --- Generated files that go with a deleted row ------------------------------
#
# Deleting a row used to leave everything the app had generated for it on
# disk: working mask, lifecycle sidecar, pyramids, SAM feature caches, Track
# preview snapshot, approved labels and submission uploads. They are now
# removed once the delete commits. What is never removed: any volume's
# registered image / label / region-mask path (even inside the data root), a
# file a surviving volume still references, anything outside the data root,
# and symlinks.

# ``<mask stem>_<axis>_<index>_<mtime>…`` — SAM 2 ``.npz`` and legacy ``.npy``
# caches, their ``.lock`` files and ``#up1024`` variants alike.
_FEATURE_CACHE_NAME = r"^{stem}_[zyx]_\d+_"


def _absolute(location: str) -> Path:
    from annotation.visualization.slice_io import resolve_path

    return Path(os.path.abspath(resolve_path(location)))


def _registered_paths(volume, *, uploads: bool) -> list[str]:
    values = [volume.image_path, volume.label_path, volume.region_mask_path]
    if uploads:
        values += [
            field.name
            for field in (volume.image_file, volume.label_file, volume.region_mask_file)
            if field
        ]
    return [value for value in values if value]


def _generated_paths(volume) -> tuple[list[str], list[str]]:
    """Root-relative ``(files, directories)`` the app generated for ``volume``."""
    from annotation.label_paths import (
        dataset_folder_rel_path,
        volume_embeddings_dir_rel_path,
        working_label_metadata_rel_path,
        working_label_rel_path,
        working_mask_stem,
    )
    from annotation.models import AnnotationTask
    from annotation.services import _tracking_preview_snapshot_rel
    from volumes.pyramid.store import LAYER_IMAGE, LAYER_REGION, pyramid_rel_path

    dataset_dir = dataset_folder_rel_path(volume.project, volume.dataset)
    stem = working_mask_stem(volume)
    working = working_label_rel_path(volume)
    snapshot = _tracking_preview_snapshot_rel(volume)
    metadata = working_label_metadata_rel_path(volume)
    files = [
        working, f"{working}.write.lock",
        snapshot, f"{snapshot}.write.lock",
        metadata, f"{metadata}.bak",
    ]
    dirs = [
        f"submissions/task_{pk}"
        for pk in AnnotationTask.objects.filter(volume=volume).values_list("pk", flat=True)
    ]
    for layer in (LAYER_IMAGE, LAYER_REGION):
        rel = pyramid_rel_path(volume, layer)
        dirs += [rel, f"{rel}.building", f"{rel}.previous"]

    approved = _absolute(f"{dataset_dir}/approved")
    if approved.is_dir():
        files += [
            f"{dataset_dir}/approved/{path.name}"
            for path in approved.glob(f"{glob.escape(stem)}_approved_s*")
        ]
    embeddings_dir = volume_embeddings_dir_rel_path(volume)
    embeddings = _absolute(embeddings_dir)
    cache_name = re.compile(_FEATURE_CACHE_NAME.format(stem=re.escape(stem)))
    if embeddings.is_dir():
        for variant in embeddings.iterdir():
            if variant.is_dir() and not variant.is_symlink():
                files += [
                    f"{embeddings_dir}/{variant.name}/{path.name}"
                    for path in variant.iterdir()
                    if cache_name.match(path.name)
                ]
    return files, dirs


def _plan_file_cleanup(volumes, *, datasets=(), project=None) -> dict | None:
    """Decide, while the rows still exist, which generated files to remove.

    ``datasets`` and ``project`` are rows being deleted outright. Their folders
    go too, but only if empty afterwards and no surviving row maps to the same
    folder name.
    """
    from annotation.label_paths import (
        dataset_folder_rel_path,
        project_folder_rel_path,
        working_label_rel_path,
    )
    from volumes.models import Volume

    try:
        dataset_dirs = [dataset_folder_rel_path(d.project, d) for d in datasets]
        doomed = list(volumes.select_related("project", "dataset"))
        doomed_ids = {volume.pk for volume in doomed}
        surviving = list(
            Volume.objects.select_related("project", "dataset").exclude(pk__in=doomed_ids)
        )
        protected = {
            _absolute(path)
            for volume in surviving
            for path in _registered_paths(volume, uploads=True)
        }
        touched_dirs = {dataset_folder_rel_path(v.project, v.dataset) for v in doomed}
        touched_dirs.update(dataset_dirs)
        # Identically named projects/datasets share a folder: never remove a
        # mask a surviving volume resolves to.
        shared = {
            working_label_rel_path(volume)
            for volume in surviving
            if dataset_folder_rel_path(volume.project, volume.dataset) in touched_dirs
        }
        files: list[str] = []
        dirs: list[str] = []
        for volume in doomed:
            if working_label_rel_path(volume) in shared:
                logger.warning(
                    "Keeping generated files of volume %s: a surviving volume "
                    "resolves to the same working mask.", volume.pk,
                )
                continue
            own_files, own_dirs = _generated_paths(volume)
            approved = {_absolute(path) for path in own_files if "/approved/" in path}
            # A deleted volume's registered sources stay; only its own approved
            # label — generated by this app — may go with it.
            protected |= {
                _absolute(path) for path in _registered_paths(volume, uploads=False)
            } - approved
            files += own_files
            dirs += own_dirs

        surviving_dataset_dirs = {
            dataset_folder_rel_path(d.project, d)
            for d in Dataset.objects.select_related("project").exclude(
                pk__in=[d.pk for d in datasets]
            )
            if project is None or d.project_id != project.pk
        }
        removable_dirs = [d for d in dataset_dirs if d not in surviving_dataset_dirs]
        project_dir = None
        if project is not None:
            project_dir = project_folder_rel_path(project)
            if any(
                project_folder_rel_path(other) == project_dir
                for other in Project.objects.exclude(pk=project.pk)
            ):
                project_dir = None
        return {
            "files": files,
            "dirs": dirs,
            "protected": protected,
            "touched_dirs": sorted(touched_dirs),
            "removable_dirs": removable_dirs,
            "project_dir": project_dir,
        }
    except Exception:
        logger.exception("Could not plan generated-file cleanup; files are kept.")
        return None


def _remove_generated_files(plan: dict) -> None:
    from annotation.cellable_port.labels_3d import forget_summary
    from annotation.visualization.slice_io import drop_file
    from core.data_root import is_owned

    root = _absolute(".")
    protected = plan["protected"]
    removed = 0
    for rel in plan["files"]:
        path = _absolute(rel)
        if path in protected or path.is_symlink() or not path.is_file() or not is_owned(path):
            continue
        drop_file(path)
        forget_summary(path)
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("Could not remove %s: %s", path, exc)
    for rel in plan["dirs"]:
        path = _absolute(rel)
        if (
            path.is_symlink() or not path.is_dir() or not is_owned(path)
            or any(p == path or p.is_relative_to(path) for p in protected)
        ):
            continue
        try:
            shutil.rmtree(path)
            removed += 1
        except OSError as exc:
            logger.warning("Could not remove %s: %s", path, exc)

    # Folders that only held generated files, deepest first; rmdir refuses
    # anything that still has content.
    empty_candidates: list[str] = []
    for dataset_dir in plan["touched_dirs"]:
        embeddings = _absolute(f"{dataset_dir}/embeddings")
        if embeddings.is_dir():
            empty_candidates += [
                f"{dataset_dir}/embeddings/{child.name}"
                for child in embeddings.iterdir()
                if child.is_dir() and not child.is_symlink()
            ]
        empty_candidates += [
            f"{dataset_dir}/{name}"
            for name in ("embeddings", "pyramids", "metadata", "approved")
        ]
    empty_candidates += plan["removable_dirs"]
    if plan["project_dir"]:
        empty_candidates.append(plan["project_dir"])
    for rel in empty_candidates:
        path = _absolute(rel)
        if path == root or path.is_symlink() or not path.is_dir() or not is_owned(path):
            continue
        try:
            path.rmdir()
        except OSError:
            pass
    if removed:
        logger.info("Removed %d generated file(s)/folder(s) after delete.", removed)


def _clean_up_after_commit(plan: dict | None) -> None:
    if plan is None:
        return

    def run():
        try:
            _remove_generated_files(plan)
        except Exception:
            logger.exception("Generated-file cleanup failed; the delete itself stands.")

    transaction.on_commit(run)


def delete_project(project: Project, *, force: bool = False) -> dict:
    counts = describe_project_dependents(project)
    _guard(f"project '{project.title}'", counts, force)
    plan = _plan_file_cleanup(
        project.volumes.all(),
        datasets=list(project.datasets.select_related("project")),
        project=project,
    )
    project.delete()
    _clean_up_after_commit(plan)
    return counts


def delete_dataset(dataset: Dataset, *, force: bool = False) -> dict:
    counts = describe_dataset_dependents(dataset)
    _guard(f"dataset '{dataset.name}'", counts, force)
    plan = _plan_file_cleanup(dataset.volumes.all(), datasets=[dataset])
    dataset.delete()
    _clean_up_after_commit(plan)
    return counts


def delete_volume(volume, *, force: bool = False) -> dict:
    from volumes.models import Volume

    counts = describe_volume_dependents(volume)
    _guard(f"volume '{volume.name}'", counts, force)
    plan = _plan_file_cleanup(Volume.objects.filter(pk=volume.pk))
    volume.delete()
    _clean_up_after_commit(plan)
    return counts
