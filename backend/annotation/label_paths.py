"""Where a volume's *working* (in-progress, editable) label copy — and the
derived artifacts that belong to it (lifecycle metadata sidecar, SAM
embedding cache) — live on disk.

Deliberately its own tiny module with no other imports from this app —
``annotation/services.py`` (the editor/tracking backend) and
``annotation/quality_control/adapters/basic.py`` (QC for in-app submissions,
which have no uploaded file to check) both need these paths, and keeping
them here instead of importing one from the other avoids any import-order
question between them.

Layout (normative — one scheme, used everywhere):

``<project name>/<dataset name>/`` under ``MITO_DATA_ROOT`` is a volume's
*dataset folder* (created at registration time; mirrors the project →
dataset → volume hierarchy the frontend shows). Inside it, per volume:

    <project>/<dataset>/
      <image stem>_mask.tif            # the working instance mask
      metadata/
        <image stem>_mask_metadata.json  # per-label lifecycle sidecar
      embeddings/
        <variant>/                     # e.g. vits
          <image stem>_z_<index>_<mtime>.npy

Two deliberate properties:

* **Beside the volume, not in a global silo.** Metadata and embeddings sit
  *inside* the volume's own dataset folder — not in a top-level
  ``data/embeddings/`` tree — so everything belonging to one volume is in
  one place, and browsing ``data/`` on disk matches what the app shows.
* **Image-derived, human-readable mask name.** The mask is named from the
  volume's registered image basename (``<stem>_mask.tif``), not
  ``volume_<id>_labels.tif`` — so a file on disk is recognisable as
  belonging to a specific image. The volume id still identifies the row in
  the DB; on disk we prefer the image name. The id only reappears in the
  filename as a **collision fallback** (``<stem>_v<id>_mask.tif``) when two
  volumes in the same dataset folder would otherwise derive the same stem
  (rare — registered image names are timestamped) — see
  :func:`working_mask_basename`.

External images: a volume's image may be registered *by reference* to an
absolute path outside ``MITO_DATA_ROOT`` (someone else's raw tree). We never
write into that tree — the working mask / metadata / embeddings always land
under this app's own dataset folder here, keyed by the image *basename*.

Volumes without a ``dataset`` (only possible for rows predating the
``Dataset`` model — see ``volumes/MODULE.md``) fall back to a ``no-dataset``
bucket per project.
"""

from __future__ import annotations

import re

_MAX_NAME_LEN = 80
WORKING_MASK_BASENAME_METADATA_KEY = "working_mask_basename"
# The only path components an OS resolves specially — must never be allowed
# to stand alone as a sanitized project/dataset directory name (they'd
# resolve to "the current directory" / "the parent directory" instead of an
# actual project/dataset folder).
_RESERVED_NAMES = {".", ".."}


def _safe_name(value: str, fallback: str) -> str:
    """A folder-name-safe version of ``value`` that still looks like the
    project/dataset's actual name (unlike a lowercased, hyphenated slug) —
    keeps spacing, case, and most punctuation, and only touches what would
    actually break as a single path component:

    - ``/`` and ``\\`` (would otherwise split into extra directory levels,
      or on Windows, be a drive/path separator) — replaced with ``-``.
    - Control characters (e.g. embedded newlines/nulls) — replaced with ``-``.
    - Leading/trailing spaces and dots — stripped (trailing dots/spaces are
      invalid or silently dropped on some filesystems; leading dots would
      make the folder look like a hidden file).

    Project/dataset names are free-text user input (``CharField``), so this
    is a real input boundary — without it, a title containing ``/`` could
    otherwise escape the intended directory or create unintended nesting.
    Falls back to ``fallback`` if nothing usable survives (empty, or exactly
    ``.``/``..``).
    """
    value = re.sub(r"[\\/\x00-\x1f]", "-", value.strip())
    value = value.strip(" .")[:_MAX_NAME_LEN].strip(" .")
    if not value or value in _RESERVED_NAMES:
        return fallback
    return value


def project_folder_rel_path(project) -> str:
    """Path (relative to ``MITO_DATA_ROOT``) of ``project``'s own folder.

    Exists so the on-disk layout mirrors the project → dataset → volume
    hierarchy the moment a project is registered — not just once someone
    starts annotating (that's when the *label* file itself first appears;
    see :func:`working_label_rel_path`).
    """
    return _safe_name(project.title, "project")


def dataset_folder_rel_path(project, dataset) -> str:
    """Path (relative to ``MITO_DATA_ROOT``) of ``dataset``'s folder within
    ``project``'s. Same rationale as :func:`project_folder_rel_path`: created
    at registration time, before any volume or label exists.
    """
    dataset_dir = _safe_name(dataset.name, "dataset") if dataset else "no-dataset"
    return f"{project_folder_rel_path(project)}/{dataset_dir}"


def _basename(location: str) -> str:
    """Last path component of a stored image/label location (handles both
    ``/`` and ``\\`` separators, absolute or relative, storage-name or path)."""
    return re.split(r"[\\/]", location or "")[-1]


def image_stem(volume) -> str:
    """A stable, human-readable stem derived from the volume's registered
    image basename — the base for the working mask / metadata / embedding
    file names.

    Strips a single trailing ``.tif`` / ``.tiff`` / ``.h5`` / ``.hdf5``
    (case-insensitive) from the image basename, keeping any inner suffix like
    ``.ome`` (so ``…volume.ome.tif`` → ``…volume.ome``, matching the on-disk
    naming brief), then folder-name-sanitises it. Falls back to
    ``volume_<id>`` when the volume has no image at all (an edge case — a
    volume normally always has an image).

    The source extension is dropped rather than kept because the working mask
    is always a TIFF whatever the image is: an HDF5 source must still yield
    ``<stem>_mask.tif``, not ``<stem>.h5_mask.tif``.
    """
    fallback = f"volume_{volume.id}"
    name = _basename(volume.image_location)
    if not name:
        return fallback
    low = name.lower()
    for ext in (".nii.gz", ".tiff", ".tif", ".hdf5", ".h5", ".nii"):
        if low.endswith(ext):
            name = name[: -len(ext)]
            break
    return _safe_name(name, fallback)


def _stem_collides(volume, stem: str) -> bool:
    """True when a *different, lower-id* volume in the same dataset folder
    derives the same ``stem`` — so this volume must disambiguate its mask
    name. Deterministic and non-colliding: the lowest-id volume keeps the
    plain stem, every later one gets ``_v<id>``. Cheap (a handful of sibling
    rows at most) and only consulted when building a path; the resulting
    memmap is cached by resolved path in ``slice_io`` regardless.
    """
    project = getattr(volume, "project", None)
    dataset_id = getattr(volume, "dataset_id", None)
    vol_id = getattr(volume, "id", None)
    if project is None or vol_id is None:
        return False
    try:
        siblings = (
            project.volumes.filter(dataset_id=dataset_id)
            .exclude(id=vol_id)
            .filter(id__lt=vol_id)
        )
        for sib in siblings:
            if image_stem(sib) == stem:
                return True
    except Exception:
        # A detached/unsaved volume (no manager for the reverse relation) can
        # never collide with a persisted sibling — treat as no collision.
        return False
    return False


def working_mask_basename(volume) -> str:
    """The working mask's filename: ``<image stem>_mask.tif`` (or
    ``<image stem>_v<id>_mask.tif`` on the rare same-folder stem collision —
    see :func:`_stem_collides`).

    If the image stem itself already ends with ``_mask`` (e.g. an
    already-masked file registered as the volume's "image" — common when
    re-importing a previously exported working mask), that trailing token is
    stripped first rather than doubled (``cortex1_mask.tif`` -> working mask
    ``cortex1_mask.tif``, not ``cortex1_mask_mask.tif``).
    """
    # Artifact identity must never depend on the *current* set of sibling DB
    # rows.  It used to: removing a duplicate registration could make a live
    # volume switch from ``<stem>_v107_mask.tif`` to ``<stem>_mask.tif`` on
    # its next read.  The old draft was still on disk, but Refresh opened a
    # freshly-seeded file at the new name and made an annotator's work appear
    # reset.  Registration/migration pins the initially selected basename in
    # Volume.metadata; once present it is the durable artifact identity.
    metadata = getattr(volume, "metadata", None) or {}
    pinned = metadata.get(WORKING_MASK_BASENAME_METADATA_KEY)
    if isinstance(pinned, str):
        pinned = _safe_name(_basename(pinned), "")
        if pinned and pinned.lower().endswith("_mask.tif"):
            return pinned

    stem = image_stem(volume)
    if stem.lower().endswith("_mask") and len(stem) > len("_mask"):
        stem = stem[: -len("_mask")]
    if _stem_collides(volume, stem):
        stem = f"{stem}_v{volume.id}"
    return f"{stem}_mask.tif"


def working_mask_stem(volume) -> str:
    """The mask filename without its ``.tif`` extension — the shared key for
    naming the metadata sidecar and embedding files that belong to this
    volume, so all of a volume's artifacts share one recognisable stem."""
    base = working_mask_basename(volume)
    return base[: -len(".tif")] if base.endswith(".tif") else base


def working_label_rel_path(volume) -> str:
    """Path (relative to ``MITO_DATA_ROOT``) of ``volume``'s working label
    copy — the file the in-app editor and SAM2 tracking always read/write,
    regardless of what ``volume.label_path``/``label_file`` (the *official*,
    approved label — see ``annotation.services.approve_submission``)
    currently points at.

    ``<project>/<dataset>/<image stem>_mask.tif``. Uniqueness within a
    dataset folder is guaranteed by the image-derived stem (with an id
    fallback on collision, :func:`working_mask_basename`); a folder-name
    collision between two identically-named projects only means their files
    share a directory, never that one file is mistaken for another's.
    """
    dataset_dir = dataset_folder_rel_path(volume.project, volume.dataset)
    return f"{dataset_dir}/{working_mask_basename(volume)}"


def approved_label_rel_path(volume, submission) -> str:
    """Immutable app-owned official label installed by one approval.

    The submission id makes every accepted result auditable and prevents a
    later approval from overwriting bytes still referenced by review history.
    """
    dataset_dir = dataset_folder_rel_path(volume.project, volume.dataset)
    name = _basename(getattr(submission.label_file, "name", ""))
    lower = name.lower()
    suffix = ".tif"
    for candidate in (".nii.gz", ".tiff", ".tif", ".hdf5", ".h5", ".npy"):
        if lower.endswith(candidate):
            suffix = candidate
            break
    return (
        f"{dataset_dir}/approved/{working_mask_stem(volume)}"
        f"_approved_s{submission.pk}{suffix}"
    )


def working_label_metadata_rel_path(volume) -> str:
    """Path (relative to ``MITO_DATA_ROOT``) of the JSON sidecar holding
    per-label lifecycle state (Proposed/Edited/Verified) for this volume's
    working label copy — mirrors Cellable's ``LabelMetadataStore.
    sidecar_path`` (``<mask>_metadata.json``) but kept in a ``metadata/``
    subfolder *beside* the mask, so the dataset folder isn't cluttered with
    a sidecar next to every mask. See
    ``annotation/cellable_port/label_state.py``."""
    from .cellable_port.label_state import LabelMetadataStore

    dataset_dir = dataset_folder_rel_path(volume.project, volume.dataset)
    sidecar_name = _basename(
        LabelMetadataStore.sidecar_path(working_mask_basename(volume))
    )
    return f"{dataset_dir}/metadata/{sidecar_name}"


def volume_embeddings_dir_rel_path(volume) -> str:
    """Path (relative to ``MITO_DATA_ROOT``) of the ``embeddings/`` folder
    for this volume's dataset — the SAM embedding cache lives under a
    ``<variant>/`` subfolder here, beside the volume's mask, not in a global
    ``data/embeddings/`` silo. See ``annotation/cellable_port/ai/embed_cache.py``.
    """
    dataset_dir = dataset_folder_rel_path(volume.project, volume.dataset)
    return f"{dataset_dir}/embeddings"


# --- Legacy paths (pre-``_mask`` scheme) — migration / lazy adoption only ---
#
# Before this scheme, the working copy was ``<dataset>/volume_<id>_labels.tif``
# with its sidecar right next to it and embeddings in a top-level
# ``embeddings/<variant>/volume_<id>/`` silo. These helpers let a one-time
# migration command (``migrate_volume_artifacts``) and the lazy-adoption path
# in ``services._writable_label`` find and relocate those without losing
# already-painted voxels. Never *write* to a legacy path — read/move only.

def legacy_working_label_rel_path(volume) -> str:
    dataset_dir = dataset_folder_rel_path(volume.project, volume.dataset)
    return f"{dataset_dir}/volume_{volume.id}_labels.tif"


def legacy_working_label_metadata_rel_path(volume) -> str:
    dataset_dir = dataset_folder_rel_path(volume.project, volume.dataset)
    return f"{dataset_dir}/volume_{volume.id}_labels_metadata.json"


def legacy_embeddings_dir_rel_path(volume, variant: str) -> str:
    """The old global embedding silo for one volume + variant:
    ``embeddings/<variant>/volume_<id>/``."""
    return f"embeddings/{variant}/volume_{volume.id}"
