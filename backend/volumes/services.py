"""Deterministic service functions for volumes and frame-based task splitting."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

from core.choices import (
    LABEL_TYPE_TO_TASK_TYPE,
    MASKED_LABEL_TYPES,
    FileFormat,
    LabelType,
)
from core.utils import inspect_volume_dtype, inspect_volume_shape, inspect_volume_voxel_size

from .models import Volume

# Only these data-file extensions may be registered for annotation. ``.nii.gz``
# is a compound extension, so extension matching uses ``str.endswith``.
# ``.h5``/``.hdf5`` are read in place (never transcoded) — see
# ``annotation/visualization/hdf5_io.py``.
SUPPORTED_DATA_EXTENSIONS = (".tif", ".tiff", ".h5", ".hdf5", ".nii", ".nii.gz")


class DataRegistrationError(ValueError):
    """Raised when an HPC directory or file fails validation."""


def _normalize_label_type(*, label_type: str | None, has_mask: bool) -> str:
    """Enforce the three supported label types and their mask coupling.

    * no mask → always ``none``
    * with mask → ``partial`` or ``prediction`` (never ``none``)
    * ``proofread`` is rejected for new writes (legacy DB rows may still hold it)

    ``label_type=None`` means *the caller did not say*, which is the normal
    case for bulk registration: an nnU-Net directory scan pairs images with
    masks by filename and has no way to know whether a mask is hand-drawn or
    model output. An unspecified mask defaults to ``prediction`` (the old
    behaviour) rather than erroring — only an *explicit* ``none`` alongside a
    mask is a contradiction worth rejecting.
    """
    if label_type is None:
        return LabelType.PREDICTION if has_mask else LabelType.NONE

    lt = label_type or LabelType.NONE
    if lt == LabelType.PROOFREAD:
        raise DataRegistrationError(
            "label_type 'proofread' is no longer supported; use none, partial, or prediction."
        )
    if has_mask:
        if lt == LabelType.NONE:
            raise DataRegistrationError(
                "label_type cannot be none when a mask is registered."
            )
        if lt not in MASKED_LABEL_TYPES:
            raise DataRegistrationError(
                "label_type must be partial or prediction when a mask is present."
            )
        return lt
    return LabelType.NONE


def _label_type_for_mask_only_edit(*, stored: str, has_mask: bool) -> str:
    """Resolve ``label_type`` for an edit that moved the *mask* but not the type.

    Deliberately never *invents* a type the caller did not ask for: silently
    turning an untyped volume into a ``prediction`` because someone attached a
    file would relabel data on the user's behalf. Two rules:

    * mask removed → ``none`` (the type described a mask that is now gone)
    * mask present → keep whatever is stored, **including legacy
      ``proofread``**. The retirement of ``proofread`` governs what a caller may
      *send*, not what history already holds; validating stored values here
      would make every legacy row uneditable.

    An untyped volume gaining its first mask is the one case with no honest
    answer, so it asks rather than guesses.
    """
    if not has_mask:
        return LabelType.NONE
    if stored and stored != LabelType.NONE:
        return stored
    raise DataRegistrationError(
        "This volume has no label_type. Send label_type ('partial' or "
        "'prediction') alongside label_path when attaching a mask."
    )


def matched_data_extension(name: str) -> str:
    """Return the supported extension a filename ends with, or ``""``."""
    lower = name.lower()
    for ext in sorted(SUPPORTED_DATA_EXTENSIONS, key=len, reverse=True):
        if lower.endswith(ext):
            return ext
    return ""


def _file_format_for(ext: str) -> str:
    if ext in (".tif", ".tiff"):
        return FileFormat.TIFF
    if ext in (".h5", ".hdf5"):
        return FileFormat.HDF5
    if ext in (".nii", ".nii.gz"):
        return FileFormat.NIFTI
    return FileFormat.OTHER


# Filename tokens that mark a file as a mask/label vs an image, used to auto-pair
# image + mask volumes that share a common base name in the same directory.
MASK_TOKENS = {
    "mask", "masks", "label", "labels", "seg", "segmentation", "segmentations",
    "gt", "groundtruth", "annotation", "annotations", "ann", "lbl",
}
IMAGE_TOKENS = {
    "image", "images", "img", "im", "raw", "data", "vol", "volume", "em", "grey",
    "gray",
}
REGION_TOKENS = {"region", "regions", "roi", "rois"}


def _stem(name: str) -> str:
    """Filename without its supported data extension."""
    ext = matched_data_extension(name)
    return name[: -len(ext)] if ext else name


def _name_tokens(stem: str) -> list[str]:
    return [t for t in re.split(r"[ _\-.]+", stem.lower()) if t]


def _is_mask_name(name: str) -> bool:
    return any(t in MASK_TOKENS for t in _name_tokens(_stem(name)))


# nnU-Net marks the channel index with a 4-digit suffix on *image* files
# (``case_0000.nii.gz``); the matching label carries no suffix (``case.nii.gz``).
# Stripping it yields the case id both sides share, which is what pairs them.
_CHANNEL_SUFFIX_RE = re.compile(r"_(\d{4})$")


def case_key(name: str) -> str:
    """The case id a file belongs to: its stem minus any channel suffix.

    ``imagesTr/jrc_mus-kidney_crop129_0000.nii.gz`` and
    ``labelsTr/jrc_mus-kidney_crop129.nii.gz`` both yield
    ``jrc_mus-kidney_crop129``, which is what makes them a pair.
    """
    return _CHANNEL_SUFFIX_RE.sub("", _stem(Path(name).name))


def pairing_key(name: str) -> str:
    """A forgiving, case-insensitive key used only for automatic pairing.

    Directory roles already tell us which side is the image and which is the
    mask, so role words in either filename are decoration rather than identity.
    Removing them from *both* sides handles exports such as
    ``heart__volume.ome.tif`` / ``heart__volume.ome_mask.tif`` while retaining
    :func:`case_key` as the user-facing volume name.
    """
    case = case_key(name)
    tokens = _name_tokens(case)
    kept = [
        t for t in tokens
        if t not in (IMAGE_TOKENS | MASK_TOKENS | REGION_TOKENS)
    ]
    if kept:
        return "_".join(kept)
    # A case may itself be a role-looking word (``vol.tif``). If a longer name
    # consists entirely of role tokens, strip only its final decoration so
    # ``vol.tif`` still pairs with ``vol_label.tif`` instead of collapsing the
    # former to ``vol`` and leaving the latter as ``vol_label``.
    if len(tokens) > 1 and tokens[-1] in (IMAGE_TOKENS | MASK_TOKENS | REGION_TOKENS):
        return "_".join(tokens[:-1])
    return case.lower()


def channel_index(name: str) -> int | None:
    """The nnU-Net channel index of a file, or None when it has no suffix."""
    match = _CHANNEL_SUFFIX_RE.search(_stem(Path(name).name))
    return int(match.group(1)) if match else None


def pair_by_case(
    image_names: list[str], mask_names: list[str]
) -> tuple[list[dict], list[str], list[str], list[str]]:
    """Pair images with masks by case id — the cross-directory workhorse.

    Images and masks may live in different directories; only their names are
    considered here. When a case has several channels the lowest one represents
    the volume and the rest are returned as ``extra_channels`` rather than being
    silently dropped.

    Returns ``(pairs, unmatched_images, unmatched_masks, extra_channels)``.
    """
    pairs: list[dict] = []
    extra_channels: list[str] = []
    matched_images: set[str] = set()
    matched_masks: set[str] = set()

    def grouped(names: list[str], key_fn) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for name in names:
            result.setdefault(key_fn(name), []).append(name)
        return result

    def add_pair(image: str, mask: str, rest: list[str] | None = None) -> None:
        pairs.append({"image": image, "mask": mask, "case": case_key(image)})
        matched_images.add(image)
        matched_masks.add(mask)
        for extra in rest or []:
            matched_images.add(extra)
            extra_channels.append(extra)

    # Prefer the established nnU-Net rule. Lowercasing fixes harmless case
    # differences without making punctuation or role-word guesses yet.
    images_by_case = grouped(image_names, lambda n: case_key(n).lower())
    masks_by_case = grouped(mask_names, lambda n: case_key(n).lower())
    for case in sorted(images_by_case):
        masks = sorted(masks_by_case.get(case, []), key=str.lower)
        if not masks:
            continue
        channels = sorted(
            images_by_case[case],
            key=lambda n: (channel_index(n) is None, channel_index(n) or 0, n.lower()),
        )
        add_pair(channels[0], masks[0], channels[1:])

    # Then try role-neutral keys for exports such as ``.ome_mask``. Only a
    # unique mask is eligible. Multiple non-channel images sharing the broad
    # key are ambiguous and stay unmatched for manual pairing instead of being
    # guessed incorrectly.
    remaining_images = [
        name for name in image_names
        if name not in matched_images and name not in extra_channels
    ]
    remaining_masks = [name for name in mask_names if name not in matched_masks]
    images_by_role_key = grouped(remaining_images, pairing_key)
    masks_by_role_key = grouped(remaining_masks, pairing_key)
    for key in sorted(images_by_role_key):
        images = sorted(
            images_by_role_key[key],
            key=lambda n: (channel_index(n) is None, channel_index(n) or 0, n.lower()),
        )
        masks = sorted(masks_by_role_key.get(key, []), key=str.lower)
        if len(masks) != 1:
            continue
        if len(images) > 1 and any(channel_index(name) is None for name in images):
            continue
        add_pair(images[0], masks[0], images[1:])

    # Finally, allow a mask key that *extends* an image key with extra
    # trailing tokens: `..._im.h5` vs `..._mask_pc2.h5` strips to
    # `..._pc2` on the mask side, because `pc2` is a pipeline suffix and not a
    # role word this module could know about. Requiring the image key to be a
    # strict token prefix keeps that from turning into "matches anything":
    # `a_b` still cannot pair with `a_c`, and both sides must be unique.
    remaining_images = [
        name for name in image_names
        if name not in matched_images and name not in extra_channels
    ]
    remaining_masks = [name for name in mask_names if name not in matched_masks]
    images_by_role_key = grouped(remaining_images, pairing_key)
    masks_by_role_key = grouped(remaining_masks, pairing_key)
    for key in sorted(images_by_role_key):
        images = images_by_role_key[key]
        if len(images) != 1:
            continue
        prefix = f"{key}_"
        extended = [k for k in masks_by_role_key if k.startswith(prefix)]
        if len(extended) != 1:
            continue
        masks = masks_by_role_key[extended[0]]
        if len(masks) != 1:
            continue
        add_pair(images[0], masks[0])

    unmatched_images = sorted(
        (n for n in image_names if n not in matched_images and n not in extra_channels),
        key=str.lower,
    )
    unmatched_masks = sorted(
        (n for n in mask_names if n not in matched_masks), key=str.lower
    )
    return (
        sorted(pairs, key=lambda p: p["case"].lower()),
        unmatched_images,
        unmatched_masks,
        sorted(extra_channels, key=str.lower),
    )


def detect_volume_pairs(filenames: list[str]) -> tuple[list[dict], list[str]]:
    """Group filenames from a *single* directory into pairs plus leftovers.

    Two conventions are supported, tried in order:

    1. nnU-Net in one folder — some files carry a ``_0000`` channel suffix and
       others do not (``vol1_0000.tiff`` + ``vol1.tiff``); the suffixed files are
       the images and the bare ones the masks.
    2. Role tokens in the name (``cortex1_image`` + ``cortex1_mask``).

    Returns ``(pairs, unpaired)`` where each pair is ``{"image", "mask", "base"}``.
    """
    suffixed = [n for n in filenames if channel_index(n) is not None]
    bare = [n for n in filenames if channel_index(n) is None]
    if suffixed and bare:
        pairs, un_images, un_masks, extras = pair_by_case(suffixed, bare)
        return (
            [{"image": p["image"], "mask": p["mask"], "base": p["case"]} for p in pairs],
            sorted(un_images + un_masks + extras, key=str.lower),
        )

    images = sorted(n for n in filenames if not _is_mask_name(n))
    masks = sorted(n for n in filenames if _is_mask_name(n))

    images_by_core: dict[str, list[str]] = {}
    for image in images:
        images_by_core.setdefault(pairing_key(image), []).append(image)

    pairs: list[dict] = []
    used_images: set[str] = set()
    used_masks: set[str] = set()
    for mask in masks:
        core = pairing_key(mask)
        candidates = [i for i in images_by_core.get(core, []) if i not in used_images]
        if len(candidates) == 1:
            image = candidates[0]
            used_images.add(image)
            used_masks.add(mask)
            pairs.append({"image": image, "mask": mask, "base": core})

    # Images with no mask and masks with no image are surfaced as unpaired, so
    # nothing silently disappears from the folder listing.
    unpaired = [f for f in images if f not in used_images]
    unpaired += [m for m in masks if m not in used_masks]

    return (
        sorted(pairs, key=lambda p: p["image"].lower()),
        sorted(unpaired, key=str.lower),
    )


def resolve_hpc_directory(hpc_directory: str) -> Path:
    """Resolve an HPC directory value (absolute, or relative to the data root).

    Validates that the value is non-empty and points to an existing directory.
    Raises :class:`DataRegistrationError` otherwise.
    """
    raw = (hpc_directory or "").strip()
    if not raw:
        raise DataRegistrationError("An HPC directory is required.")
    path = Path(raw)
    if not path.is_absolute():
        path = Path(settings.MITO_DATA_ROOT) / raw
    try:
        path = path.resolve()
    except OSError as exc:
        raise DataRegistrationError(f"Cannot access directory: {raw} ({exc})") from exc
    if not path.exists():
        raise DataRegistrationError(f"Directory does not exist: {raw}")
    if not path.is_dir():
        raise DataRegistrationError(f"Not a directory: {raw}")
    if not os.access(path, os.R_OK | os.X_OK):
        raise DataRegistrationError(
            f"Permission denied reading directory: {raw}. "
            "The app process must be able to list that folder "
            "(e.g. chmod o+rx, or ACL for the service user)."
        )
    return path


def _stored_path(abs_path: Path) -> str:
    """Store a path relative to the data root when possible, else absolute."""
    root = Path(settings.MITO_DATA_ROOT).resolve()
    try:
        return str(abs_path.resolve().relative_to(root))
    except ValueError:
        return str(abs_path.resolve())


# --- nnU-Net dataset.json -------------------------------------------------
#
# An nnU-Net dataset root sits one level above imagesTr/labelsTr and carries a
# dataset.json listing `training: [{image, label}]`. That list is authoritative:
# it pairs files without any name guessing, and its descriptive fields save the
# requester retyping metadata that is already recorded.

# Directory-name prefixes that mark a folder's role, and the split they imply.
_SPLIT_SUFFIXES = {"tr": "train", "ts": "test"}


def split_for_directory(name: str) -> str:
    """The nnU-Net split a directory name implies ('train'/'test'), else ''."""
    lowered = name.lower()
    for suffix, split in _SPLIT_SUFFIXES.items():
        if lowered.endswith(suffix) and (
            lowered.startswith("images") or lowered.startswith("labels")
        ):
            return split
    return ""


def _looks_like_mask_dir(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(
        ("label", "mask", "seg", "gt", "annotation", "region", "roi")
    )


def _looks_like_image_dir(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(("image", "img", "raw", "em"))


def read_dataset_manifest(directory: Path) -> dict | None:
    """Load ``dataset.json`` from ``directory`` or its parent, if present.

    Returns ``{"path", "pairs", "metadata"}`` where ``pairs`` are
    ``{"image", "mask", "image_dir", "mask_dir"}`` entries taken verbatim from
    the manifest's ``training`` list, or None when there is no readable manifest.
    """
    for candidate in (directory / "dataset.json", directory.parent / "dataset.json"):
        if not candidate.is_file():
            continue
        try:
            raw = json.loads(candidate.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue

        pairs = []
        for entry in raw.get("training") or []:
            if not isinstance(entry, dict):
                continue
            image, label = entry.get("image"), entry.get("label")
            if not image or not label:
                continue
            pairs.append(
                {
                    "image": Path(image).name,
                    "mask": Path(label).name,
                    "image_dir": Path(image).parent.name,
                    "mask_dir": Path(label).parent.name,
                    "case": case_key(image),
                }
            )

        metadata = {}
        for src, dest in (
            ("description", "description"),
            ("reference", "publication"),
            ("licence", "licence"),
            ("name", "dataset_source"),
        ):
            value = raw.get(src)
            if isinstance(value, str) and value.strip():
                metadata[dest] = value.strip()
        # Class and channel maps are structured, not free text; keep them as-is.
        if isinstance(raw.get("labels"), dict):
            metadata["label_classes"] = raw["labels"]
        if isinstance(raw.get("channel_names"), dict):
            metadata["channel_names"] = raw["channel_names"]

        return {"path": _stored_path(candidate), "pairs": pairs, "metadata": metadata}
    return None


def suggest_sibling_directories(directory: Path) -> dict:
    """Sibling folders that look like image/mask sets, for quick-picking.

    Lets a requester who typed ``.../imagesTr`` choose ``labelsTr`` vs
    ``labelsTr-instance`` (or the Ts split) without hunting for the path.
    """
    images, masks = [], []
    try:
        siblings = sorted(directory.parent.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return {"images": [], "masks": []}

    for entry in siblings:
        if not entry.is_dir():
            continue
        count = sum(1 for f in entry.iterdir() if f.is_file() and matched_data_extension(f.name)) \
            if _looks_like_mask_dir(entry.name) or _looks_like_image_dir(entry.name) else 0
        if not count:
            continue
        item = {
            "name": entry.name,
            "path": _stored_path(entry),
            "count": count,
            "split": split_for_directory(entry.name),
            "current": entry.resolve() == directory.resolve(),
        }
        if _looks_like_mask_dir(entry.name):
            masks.append(item)
        elif _looks_like_image_dir(entry.name):
            images.append(item)
    return {"images": images, "masks": masks}


def _list_data_files(directory: Path) -> list[dict]:
    """Supported data files directly inside ``directory``."""
    files = []
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except PermissionError as exc:
        raise DataRegistrationError(
            f"Permission denied reading directory: {directory}. "
            "The app process must be able to list that folder "
            "(e.g. chmod o+rx, or ACL for the service user)."
        ) from exc
    for entry in entries:
        if not entry.is_file():
            continue
        ext = matched_data_extension(entry.name)
        if not ext:
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            size = 0
        files.append(
            {
                "name": entry.name,
                "path": _stored_path(entry),
                "extension": ext,
                "size": size,
            }
        )
    return files


def scan_data_sources(
    image_directory: str,
    mask_directory: str = "",
    region_mask_directory: str = "",
    *,
    auto_pair: bool = False,
) -> dict:
    """List source files and return deterministic pairing suggestions.

    Pairing is advisory: the current UI intentionally starts editable/region
    selectors empty, while legacy API clients still consume ``pairs`` and
    ``region_by_image``. Keeping both in one response preserves the old
    registration contract without forcing the suggestions into the UI.
    """
    image_dir = resolve_hpc_directory(image_directory)
    mask_dir = resolve_hpc_directory(mask_directory) if (mask_directory or "").strip() else None
    region_mask_dir = (
        resolve_hpc_directory(region_mask_directory)
        if (region_mask_directory or "").strip()
        else None
    )

    image_files = _list_data_files(image_dir)
    image_names = [f["name"] for f in image_files]

    if mask_dir is None:
        mask_files = []
    elif mask_dir == image_dir:
        mask_files = list(image_files)
    else:
        mask_files = _list_data_files(mask_dir)

    if region_mask_dir is None:
        region_mask_files = []
    elif region_mask_dir == image_dir:
        region_mask_files = list(image_files)
    else:
        region_mask_files = _list_data_files(region_mask_dir)

    manifest = read_dataset_manifest(image_dir)

    if not auto_pair:
        return {
            "image_directory": _stored_path(image_dir),
            "region_mask_directory": (
                _stored_path(region_mask_dir) if region_mask_dir is not None else ""
            ),
            "mask_directory": _stored_path(mask_dir) if mask_dir is not None else "",
            "image_files": image_files,
            "region_mask_files": region_mask_files,
            "mask_files": mask_files,
            "pairs": [],
            "region_by_image": {},
            "unmatched_images": sorted(image_names, key=str.lower),
            "unmatched_region_masks": sorted(
                (f["name"] for f in region_mask_files), key=str.lower
            ),
            "unmatched_masks": sorted(
                (f["name"] for f in mask_files), key=str.lower
            ),
            "extra_channels": [],
            "pairing_source": "manual",
            "split": split_for_directory(image_dir.name),
            "suggestions": suggest_sibling_directories(image_dir),
            "dataset_metadata": (manifest or {}).get("metadata") or {},
            "manifest_path": (manifest or {}).get("path", ""),
        }

    pairing_source = "filename"
    if mask_dir is None or mask_dir == image_dir:
        detected, unmatched_images = detect_volume_pairs(image_names)
        pairs = [
            {"image": p["image"], "mask": p["mask"], "case": p["base"]}
            for p in detected
        ]
        detected_masks = {p["mask"] for p in pairs}
        if mask_dir is None:
            mask_files = [f for f in image_files if f["name"] in detected_masks]
        unmatched_masks: list[str] = []
        extra_channels: list[str] = []
    else:
        mask_names = [f["name"] for f in mask_files]
        manifest_pairs = _manifest_pairs_for(
            manifest, image_dir, mask_dir, image_names, mask_names
        )
        if manifest_pairs is not None:
            pairs = manifest_pairs
            pairing_source = "dataset.json"
            used_images = {p["image"] for p in pairs}
            used_masks = {p["mask"] for p in pairs}
            unmatched_images = sorted(set(image_names) - used_images, key=str.lower)
            unmatched_masks = sorted(set(mask_names) - used_masks, key=str.lower)
            extra_channels = []
        else:
            pairs, unmatched_images, unmatched_masks, extra_channels = pair_by_case(
                image_names, mask_names
            )

    region_by_image: dict[str, str] = {}
    unmatched_region_masks = [f["name"] for f in region_mask_files]
    if region_mask_dir is not None:
        region_pairs, _, unmatched_region_masks, _ = pair_by_case(
            image_names, unmatched_region_masks
        )
        region_by_image = {
            pair["image"]: pair["mask"] for pair in region_pairs
        }

    return {
        "image_directory": _stored_path(image_dir),
        "region_mask_directory": (
            _stored_path(region_mask_dir) if region_mask_dir is not None else ""
        ),
        "mask_directory": _stored_path(mask_dir) if mask_dir is not None else "",
        "image_files": image_files,
        "region_mask_files": region_mask_files,
        "mask_files": mask_files,
        "pairs": pairs,
        "region_by_image": region_by_image,
        "unmatched_images": unmatched_images,
        "unmatched_region_masks": unmatched_region_masks,
        "unmatched_masks": unmatched_masks,
        "extra_channels": extra_channels,
        "pairing_source": pairing_source,
        "split": split_for_directory(image_dir.name),
        "suggestions": suggest_sibling_directories(image_dir),
        "dataset_metadata": (manifest or {}).get("metadata") or {},
        "manifest_path": (manifest or {}).get("path", ""),
    }


def _manifest_pairs_for(
    manifest: dict | None,
    image_dir: Path,
    mask_dir: Path,
    image_names: list[str],
    mask_names: list[str],
) -> list[dict] | None:
    """Manifest pairs, but only when they describe these two folders.

    Picking ``labelsTr-instance`` when the manifest documents ``labelsTr`` means
    the manifest does not apply; the caller falls back to name matching.
    """
    if not manifest or not manifest.get("pairs"):
        return None
    available_images, available_masks = set(image_names), set(mask_names)
    pairs = []
    for entry in manifest["pairs"]:
        if entry["image_dir"] != image_dir.name or entry["mask_dir"] != mask_dir.name:
            return None
        # A manifest listing files that are not on disk is stale, not authoritative.
        if entry["image"] not in available_images or entry["mask"] not in available_masks:
            return None
        pairs.append({"image": entry["image"], "mask": entry["mask"], "case": entry["case"]})
    return pairs or None


def scan_hpc_directory(hpc_directory: str) -> dict:
    """Single-directory scan (legacy shape, kept for existing callers)."""
    result = scan_data_sources(hpc_directory, auto_pair=True)
    return {
        "directory": result["image_directory"],
        "files": result["image_files"],
        "pairs": [
            {"image": p["image"], "mask": p["mask"], "base": p["case"]}
            for p in result["pairs"]
        ],
        "unpaired": result["unmatched_images"],
    }


@transaction.atomic
def register_dataset(
    *,
    created_by,
    dataset: str,
    volume: str = "",
    image_directory: str = "",
    region_mask_directory: str = "",
    mask_directory: str = "",
    hpc_directory: str = "",
    pairs: list[dict] | None = None,
    files: list[dict] | None = None,
    metadata: dict | None = None,
    project=None,
    annotation_type: str | None = None,
    label_type: str | None = None,
    description: str = "",
    reviewed: bool = False,
) -> tuple:
    """Register HPC file references as volumes under a dataset.

    ``dataset`` is required, as is an image directory — given either as
    ``image_directory`` or, for older callers, ``hpc_directory``.

    ``volume`` is accepted for backwards compatibility but ignored: each
    registered file is its own volume. Optional per-entry ``name`` (or the
    deprecated ``chunk_id`` alias) renames that volume; otherwise the case id
    from the filename is used. Masks may live in a **separate**
    ``mask_directory`` (the usual nnU-Net ``imagesTr``/``labelsTr`` split);
    when it is omitted, masks are looked for alongside the images. Every
    referenced file must be a supported ``.tif``/``.tiff``/``.nii.gz`` file in
    its own directory.

    Registration is flexible about image/mask pairing:

    * ``pairs`` — explicit ``[{image, mask?, name?}, ...]`` entries. Each
      becomes one volume; when ``mask`` is given it is stored as the label,
      resolved against ``mask_directory``.
    * ``files`` — image-only ``[{path|name, name?}, ...]`` entries (no masks).
    * neither — the directories are scanned and **all detected image+mask pairs
      plus any unpaired images** are registered.

    ``label_type`` is applied to volumes that get a mask (defaulting to
    ``prediction`` so they become proofreading tasks). Returns ``(project,
    [Volume, ...])``; a new project is created unless ``project`` is given, and
    the volumes are grouped under a :class:`projects.Dataset` named ``dataset``.
    """
    from projects.services import create_project, get_or_create_dataset

    dataset = (dataset or "").strip()
    volume = (volume or "").strip()
    if not dataset:
        raise DataRegistrationError("A dataset name is required.")

    image_source = (image_directory or hpc_directory or "").strip()
    if not image_source:
        raise DataRegistrationError("An image directory is required.")
    directory = resolve_hpc_directory(image_source)
    mask_source = (mask_directory or "").strip()
    mask_dir = resolve_hpc_directory(mask_source) if mask_source else directory
    region_mask_source = (region_mask_directory or "").strip()
    region_mask_dir = (
        resolve_hpc_directory(region_mask_source)
        if region_mask_source
        else directory
    )

    def _resolve(raw_name: str, kind: str = "file", *, where: Path | None = None):
        name = (raw_name or "").strip()
        if not name:
            raise DataRegistrationError(f"A {kind} name is required.")
        base = Path(name).name  # basenames only; no path traversal
        ext = matched_data_extension(base)
        if not ext:
            raise DataRegistrationError(
                f"Unsupported file type: {base}. Only .tif, .tiff, .h5, "
                ".hdf5, .nii, and .nii.gz are accepted."
            )
        candidate = ((where or directory) / base).resolve()
        if not candidate.is_file():
            raise DataRegistrationError(f"File not found in directory: {base}")
        return base, candidate, ext

    # Build the normalised list of entries:
    #   (image_base, image_path, image_ext, region_path|None, mask_path|None, volume_name)
    def _entry_name(item: dict) -> str:
        # Prefer explicit ``name``; accept deprecated ``chunk_id`` as an alias.
        return (item.get("name") or item.get("chunk_id") or "").strip()

    entries: list[tuple] = []
    if pairs:
        for item in pairs:
            image_base, image_path, image_ext = _resolve(item.get("image"), "image")
            region_name = (item.get("region_mask") or "").strip()
            region_path = (
                _resolve(region_name, "region mask", where=region_mask_dir)[1]
                if region_name
                else None
            )
            mask_name = (item.get("mask") or "").strip()
            if mask_name:
                mask_base, mask_path, _ = _resolve(mask_name, "mask", where=mask_dir)
            else:
                mask_base = mask_path = None
            entries.append(
                (image_base, image_path, image_ext, region_path, mask_path,
                 _entry_name(item))
            )
    elif files:
        for item in files:
            image_base, image_path, image_ext = _resolve(
                item.get("path") or item.get("name"), "image"
            )
            # For image-only file rows, ``name`` may be the path; prefer chunk_id
            # / an explicit rename that is not the path basename.
            rename = (item.get("chunk_id") or "").strip()
            if not rename:
                # ``name`` on file rows historically meant the image path/name.
                # Only treat it as a rename when it differs from the file key.
                candidate = (item.get("name") or "").strip()
                file_key = (item.get("path") or item.get("name") or "").strip()
                rename = candidate if candidate and candidate != file_key else ""
            entries.append(
                (image_base, image_path, image_ext, None, None, rename)
            )
    else:
        # No explicit pairs from the client: fall back to filename / manifest
        # pairing for API callers that register a whole directory at once.
        # The Register Data UI always sends explicit pairs and does not use this.
        image_names = [f["name"] for f in _list_data_files(directory)]
        separate = mask_dir != directory
        if separate:
            mask_names = [f["name"] for f in _list_data_files(mask_dir)]
            manifest = read_dataset_manifest(directory)
            manifest_pairs = _manifest_pairs_for(
                manifest, directory, mask_dir, image_names, mask_names
            )
            if manifest_pairs is not None:
                auto_pairs = manifest_pairs
                unmatched = [
                    n for n in image_names
                    if n not in {p["image"] for p in auto_pairs}
                ]
            else:
                auto_pairs, unmatched, _, _ = pair_by_case(image_names, mask_names)
        else:
            detected, unmatched = detect_volume_pairs(image_names)
            auto_pairs = [
                {"image": p["image"], "mask": p["mask"], "case": p["base"]}
                for p in detected
            ]

        region_names = (
            [f["name"] for f in _list_data_files(region_mask_dir)]
            if region_mask_source and region_mask_dir != directory
            else [f["name"] for f in _list_data_files(directory) if _is_mask_name(f["name"])]
            if region_mask_source
            else []
        )
        region_by_image: dict[str, str] = {}
        if region_names:
            region_pairs, _, _, _ = pair_by_case(image_names, region_names)
            region_by_image = {pair["image"]: pair["mask"] for pair in region_pairs}

        for pair in auto_pairs:
            image_base, image_path, image_ext = _resolve(pair["image"], "image")
            mask_base, mask_path, _ = _resolve(pair["mask"], "mask", where=mask_dir)
            region_name = region_by_image.get(pair["image"], "")
            region_path = (
                _resolve(region_name, "region mask", where=region_mask_dir)[1]
                if region_name
                else None
            )
            entries.append(
                (image_base, image_path, image_ext, region_path, mask_path, "")
            )
        for name in unmatched:
            image_base, image_path, image_ext = _resolve(name, "image")
            region_name = region_by_image.get(name, "")
            region_path = (
                _resolve(region_name, "region mask", where=region_mask_dir)[1]
                if region_name
                else None
            )
            entries.append((image_base, image_path, image_ext, region_path, None, ""))

    if not entries:
        raise DataRegistrationError(
            "No supported files (.tif, .tiff, .h5, .hdf5, .nii, .nii.gz) found to register."
        )

    if project is None:
        project = create_project(
            title=dataset,
            dataset=dataset,
            created_by=created_by,
            description=description,
            annotation_type=annotation_type,
            # Metadata describes the data, not the project, so it goes on the
            # dataset below — a project may hold several with differing values.
            metadata={},
            reviewed=reviewed,
        )

    # Metadata describes *this* data, so it belongs to the dataset. Registering
    # again under the same name adds volumes to the existing dataset.
    dataset_row = get_or_create_dataset(
        project=project,
        name=dataset,
        description=description,
        metadata=metadata or {},
        image_directory=_stored_path(directory),
        region_mask_directory=(
            _stored_path(region_mask_dir) if region_mask_source else ""
        ),
        mask_directory=_stored_path(mask_dir) if mask_source else "",
    )

    # Per-volume: image-only → none; masked → partial|prediction (never none).
    # proofread is no longer accepted for new registrations.

    # Which nnU-Net split these files came from, so train and test data stay
    # distinguishable once registered.
    split = split_for_directory(directory.name)

    created = []
    for image_base, image_path, image_ext, region_mask_path, mask_path, volume_name in entries:
        if region_mask_path is not None:
            image_shape = inspect_volume_shape(image_path)
            region_shape = inspect_volume_shape(region_mask_path)
            if (
                image_shape is not None
                and region_shape is not None
                and image_shape != region_shape
            ):
                raise DataRegistrationError(
                    f"Region mask shape {region_shape} does not match raw image "
                    f"shape {image_shape} for {image_base}."
                )
        has_mask = mask_path is not None
        # The case id names the volume: 'case_00', not 'case_00_0000.tiff'.
        case = case_key(image_base)
        vol = register_volume(
            project=project,
            dataset=dataset_row,
            name=volume_name or case,
            image_path=_stored_path(image_path),
            region_mask_path=(
                _stored_path(region_mask_path)
                if region_mask_path is not None
                else ""
            ),
            label_path=_stored_path(mask_path) if has_mask else "",
            label_type=_normalize_label_type(
                label_type=label_type,
                has_mask=has_mask,
            ),
            file_format=_file_format_for(image_ext),
            metadata={"split": split} if split else None,
            created_by=created_by,
        )
        created.append(vol)

    return project, created


def register_volume(
    *,
    project=None,
    dataset=None,
    name: str,
    image_path: str = "",
    image_file=None,
    region_mask_path: str = "",
    region_mask_file=None,
    label_path: str = "",
    label_file=None,
    label_type: str | None = None,
    file_format: str | None = None,
    voxel_size=None,
    metadata: dict | None = None,
    autodetect_shape: bool = True,
    created_by=None,
    enqueue_pyramid: bool = True,
) -> Volume:
    """Register (or upload) an image volume under a project and dataset.

    Either ``project`` or ``dataset`` must be given; the project is taken from
    the dataset when omitted, keeping the denormalised FK consistent.

    ``voxel_size`` may be a ``(z, y, x)`` tuple. If ``autodetect_shape`` is set
    and the image is a readable TIFF under ``MITO_DATA_ROOT``, the ``(x, y, z)``
    shape is filled in automatically.
    """
    if project is None and dataset is None:
        raise DataRegistrationError("A project or dataset is required.")
    if project is None:
        project = dataset.project

    has_mask = bool(label_path) or label_file is not None
    label_type = _normalize_label_type(label_type=label_type, has_mask=has_mask)

    if dataset is not None and dataset.project_id != project.pk:
        raise DataRegistrationError("The dataset does not belong to the project.")

    values = {
        "project": project,
        "name": name,
        "region_mask_path": region_mask_path or "",
        "label_path": label_path or "",
        "label_type": label_type,
        "metadata": metadata or {},
    }
    if image_file is not None:
        values["image_file"] = image_file
    if region_mask_file is not None:
        values["region_mask_file"] = region_mask_file
    if label_file is not None:
        values["label_file"] = label_file
    if file_format is not None:
        values["file_format"] = file_format
    if voxel_size is not None:
        values["voxel_size_z"], values["voxel_size_y"], values["voxel_size_x"] = voxel_size

    # A registered source path is the stable identity of a volume inside its
    # dataset.  The database constraint makes this safe even when a proxy times
    # out and two identical POSTs overlap: the second request replays the row
    # that won instead of creating another Volume and another annotation task.
    if dataset is not None and image_path:
        volume, created = Volume.objects.get_or_create(
            dataset=dataset,
            image_path=image_path,
            defaults=values,
        )
        if not created:
            expected = {
                "project_id": project.pk,
                "name": name,
                "region_mask_path": region_mask_path or "",
                "label_path": label_path or "",
                "label_type": label_type,
            }
            conflicts = [
                field for field, value in expected.items()
                if getattr(volume, field) != value
            ]
            if conflicts:
                raise DataRegistrationError(
                    f"{image_path} is already registered in this dataset with "
                    f"different {', '.join(conflicts)}. Edit the existing volume "
                    "instead of registering the same source again."
                )
            _pin_working_mask_basename(volume)
            return volume
    else:
        volume = Volume(dataset=dataset, image_path=image_path or "", **values)
        volume.save()

    _pin_working_mask_basename(volume)

    if autodetect_shape and any(value is None for value in (
        volume.shape_z, volume.shape_y, volume.shape_x,
        volume.voxel_size_z, volume.voxel_size_y, volume.voxel_size_x,
    )):
        _try_autodetect_shape(volume)

    if enqueue_pyramid:
        enqueue_pyramid_build(volume, actor=created_by)
        # The ROI is a second read-only layer with the same derivative
        # contract; queued separately so a missing or failing region mask can
        # never hold up the image the editor opens with.
        enqueue_pyramid_build(volume, actor=created_by, layer="region")

    return volume


def _pin_working_mask_basename(volume: Volume) -> None:
    """Persist the on-disk artifact name once; never recompute it later."""
    from annotation.label_paths import (
        WORKING_MASK_BASENAME_METADATA_KEY,
        working_mask_basename,
    )

    metadata = dict(volume.metadata or {})
    if metadata.get(WORKING_MASK_BASENAME_METADATA_KEY):
        return
    metadata[WORKING_MASK_BASENAME_METADATA_KEY] = working_mask_basename(volume)
    volume.metadata = metadata
    volume.save(update_fields=["metadata"])


def pyramid_idempotency_key(volume_id: int, *, layer: str, trigger: str) -> str:
    """One key per *occasion*, volume and layer.

    Registration, a replaced region mask and a backfill are three different
    builds of the same layer. Sharing one key made the later two replay the
    registration job instead of queueing anything — the derivative was cleared
    and nothing was ever rebuilt. The registration key keeps its original
    spelling so jobs submitted before this split still match their own trigger.
    """
    suffix = "" if layer == "image" else f"-{layer}"
    prefix = "register" if trigger == "registration" else trigger
    return f"{prefix}-volume-{volume_id}{suffix}"


def enqueue_pyramid_build(
    volume: Volume, *, actor=None, layer: str = "image", trigger: str = "registration"
):
    """Queue the additive derivative; a dispatcher performs the heavy build.

    Registration must remain fast and successful even if job submission has a
    transient failure. Such a volume stays honestly ``not_built`` and can be
    retried from its manager page. A region build is only meaningful when the
    volume has a region mask, so it is skipped rather than queued to fail.

    A build already in flight for this layer is not an error: the caller wanted
    one queued, and one is. Its job is returned rather than logged as a failure.
    """
    if not getattr(settings, "FEATURE_VOLUME_PYRAMIDS", False):
        return None
    if layer == "region" and not volume.has_region_mask:
        return None
    from volumes.pyramid.jobs import DuplicateBuild, PyramidBuildSpec, submit_build

    try:
        return submit_build(
            PyramidBuildSpec(
                volume_id=volume.pk,
                layer=layer,
                idempotency_key=pyramid_idempotency_key(
                    volume.pk, layer=layer, trigger=trigger
                ),
                metadata={"trigger": trigger},
            ),
            actor=actor,
        )
    except DuplicateBuild as exc:
        from processing.models import ProcessingJob

        logger.info(
            "A %s pyramid build is already active for volume %s; kept job %s",
            layer,
            volume.pk,
            exc.job_id,
        )
        return ProcessingJob.objects.filter(pk=exc.job_id).first()
    except Exception:
        logger.exception(
            "Could not enqueue %s pyramid build for volume %s", layer, volume.pk
        )
        return None


def volumes_with_region_mask(queryset=None):
    """``Volume`` rows whose ``has_region_mask`` property is true.

    Spelled as a ``Q`` rather than ``exclude(path="", file="")`` because
    ``region_mask_file`` is nullable: under three-valued logic a ``NULL`` file
    makes that exclusion neither true nor false, and the volume — which has a
    perfectly good registered *path* — silently drops out of the result.
    """
    from django.db.models import Q

    base = Volume.objects.all() if queryset is None else queryset
    return base.filter(
        ~Q(region_mask_path="")
        | (Q(region_mask_file__isnull=False) & ~Q(region_mask_file=""))
    )


def volumes_missing_region_pyramid(queryset=None):
    """Online volumes that have an ROI but do not stream it yet.

    This is the backfill's definition of eligible, and deliberately the same
    condition the manager page renders as **ROI Not built**.
    """
    return volumes_with_region_mask(queryset).filter(region_ready_streaming=False)


def backfill_region_pyramids(
    *, queryset=None, limit: int = 0, actor=None, dry_run: bool = False
) -> dict:
    """Queue the region build every eligible volume should already have had.

    Volumes registered before the ROI layer existed only ever got an image job,
    and re-registering them to earn one would mean re-registering data that is
    already online and assigned. So the enqueue that registration performs is
    offered as an operation in its own right, over exactly the rows that are
    missing it.

    Nothing is built here: each volume gets a *queued* job, and the dispatcher
    applies its own ``--max-new`` concurrency. ``limit`` caps how many are
    queued per run for an operator who wants to feed the queue in batches.

    Returns a report of volume ids by outcome. ``in_flight`` volumes already had
    an active build and are left alone — running this twice is safe.
    """
    from volumes.pyramid.jobs import ACTIVE_STATUSES, job_layer

    eligible = list(
        volumes_missing_region_pyramid(queryset).order_by("pk")
    )
    report = {
        "eligible": [volume.pk for volume in eligible],
        "queued": [],
        "in_flight": [],
        "skipped": [],
    }
    if not eligible:
        return report

    from core.choices import ProcessingJobType
    from processing.models import ProcessingJob

    active = {
        job.volume_id
        for job in ProcessingJob.objects.filter(
            job_type=ProcessingJobType.BUILD_PYRAMID,
            status__in=ACTIVE_STATUSES,
            volume_id__in=[volume.pk for volume in eligible],
        )
        if job_layer(job) == "region"
    }

    for volume in eligible:
        if volume.pk in active:
            report["in_flight"].append(volume.pk)
            continue
        if limit and len(report["queued"]) >= limit:
            report["skipped"].append(volume.pk)
            continue
        if dry_run:
            report["queued"].append(volume.pk)
            continue
        job = enqueue_pyramid_build(
            volume, actor=actor, layer="region", trigger="backfill"
        )
        if job is None:
            report["skipped"].append(volume.pk)
        else:
            report["queued"].append(volume.pk)
    return report


def volume_image_file(volume: Volume) -> Path | None:
    """Absolute path of a volume's source image, or ``None`` when unset.

    ``image_file`` names are relative to ``MITO_DATA_ROOT``; ``image_path`` may
    be absolute (an HPC reference registered in place) or relative to the same
    root.
    """
    location = volume.image_location
    if not location:
        return None
    candidate = Path(location)
    if not candidate.is_absolute():
        candidate = Path(settings.MITO_DATA_ROOT) / location
    return candidate


def _unreadable_reason(path: Path) -> str:
    """Why *this process* cannot read ``path``, phrased as something to fix.

    Registration references files this app does not own (``/home/.../data/...``
    on an HPC share), and the service account is not the one that created them.
    A ``0640`` source file in a world-listable directory therefore scans fine
    and then reads as "no shape" — which surfaces only as a volume that can
    never be assigned. Naming the cause is the difference between a five-minute
    ACL fix and an afternoon.
    """
    if not path.exists():
        return "the file does not exist at that path"
    if not os.access(path, os.R_OK):
        return (
            "the application process cannot read that file (grant it read "
            "access, e.g. setfacl -m u:<service-user>:r on the file or -R rX "
            "on the directory)"
        )
    return "its header could not be parsed as a supported volume"


def _try_autodetect_shape(volume: Volume) -> bool:
    """Fill shape and voxel size from the image file when they can be read.

    Shape comes from TIFF/HDF5 headers; voxel size from TIFF resolution/ImageJ
    metadata, HDF5 ``element_size_um``, or NIfTI pixdim. Each is only filled
    when it is still blank, so a manually-entered value is never overwritten.

    Returns whether the volume now has a ``shape_z``. A volume without one is
    skipped by ``ensure_volume_tasks`` and can never be assigned, so failing to
    read it is logged as a warning naming the likely cause — this used to be
    entirely silent, and a directory of unreadable ``.h5`` files registered
    "successfully" into volumes no manager could turn into tasks.
    """
    candidate = volume_image_file(volume)
    if candidate is None:
        return bool(volume.shape_z)

    changed: list[str] = []
    if any(value is None for value in (volume.shape_z, volume.shape_y, volume.shape_x)):
        shape = inspect_volume_shape(candidate)
        if shape is not None:
            volume.shape_x, volume.shape_y, volume.shape_z = shape
            changed += ["shape_x", "shape_y", "shape_z"]

    if any(value is None for value in (
        volume.voxel_size_z, volume.voxel_size_y, volume.voxel_size_x
    )):
        voxel = inspect_volume_voxel_size(candidate)
        if voxel is not None:
            z, y, x = voxel
            if z is not None:
                volume.voxel_size_z = z
                changed.append("voxel_size_z")
            if y is not None:
                volume.voxel_size_y = y
                changed.append("voxel_size_y")
            if x is not None:
                volume.voxel_size_x = x
                changed.append("voxel_size_x")

    dtype = inspect_volume_dtype(candidate)
    if dtype and volume.metadata.get("dtype") != dtype:
        volume.metadata = {**volume.metadata, "dtype": dtype}
        changed.append("metadata")

    if changed:
        volume.save(update_fields=changed)

    if not volume.shape_z:
        logger.warning(
            "Volume %s (%s) has no detectable shape: %s. Tasks cannot be "
            "created for it until the shape is known.",
            volume.pk, candidate, _unreadable_reason(candidate),
        )
        return False
    return True


def ensure_volume_shape(volume: Volume) -> bool:
    """Make sure ``volume`` has a shape, re-reading the file if it does not.

    Autodetection happens at registration, which is exactly when it is most
    likely to fail for reasons that get fixed afterwards — a permission grant,
    a mount that was not up yet, a file still being copied. Without a second
    attempt those volumes stay unassignable forever even though the underlying
    problem is long gone, and the only recovery is re-registering them.

    Cheap when it succeeds (headers only, and ``core.utils`` caches per file)
    and a no-op once the shape is recorded.
    """
    if volume.shape_z:
        return True
    return _try_autodetect_shape(volume)


def update_volume_metadata(volume: Volume, **fields) -> Volume:
    """Update whitelisted volume fields.

    ``image_path``/``label_path`` are editable so a wrong pairing can be fixed
    without re-registering, and ``dataset`` so a volume can be moved to the
    right dataset (its project follows).
    """
    allowed = {
        "name",
        "label_type",
        "image_path",
        "region_mask_path",
        "label_path",
        "file_format",
        "shape_x",
        "shape_y",
        "shape_z",
        "voxel_size_x",
        "voxel_size_y",
        "voxel_size_z",
        "status",
    }
    changed = []
    pending_label_type = fields.get("label_type", None)
    pending_label_path = fields.get("label_path", None)
    if pending_label_type is not None or pending_label_path is not None:
        # Re-validate the mask ↔ label_type coupling after the edit. Which rule
        # applies depends on whether the *caller* named a type: a stated type is
        # validated strictly, an unstated one is carried over rather than
        # invented (see _label_type_for_mask_only_edit).
        next_path = (
            pending_label_path
            if pending_label_path is not None
            else (volume.label_path or "")
        )
        has_mask = bool(next_path)
        if pending_label_type is not None:
            resolved = _normalize_label_type(
                label_type=pending_label_type, has_mask=has_mask
            )
        else:
            resolved = _label_type_for_mask_only_edit(
                stored=volume.label_type, has_mask=has_mask
            )
        fields = {**fields, "label_type": resolved}

    for key, value in fields.items():
        if key == "metadata":
            volume.metadata = {**(volume.metadata or {}), **(value or {})}
            changed.append("metadata")
        elif key == "dataset" and value is not None:
            volume.dataset = value
            # The project is denormalised from the dataset; keep them in step.
            volume.project = value.project
            changed.extend(["dataset", "project"])
        elif key in allowed:
            setattr(volume, key, value)
            changed.append(key)
    if changed:
        volume.save(update_fields=changed)
    if "region_mask_path" in changed:
        try:
            from .region_masks import refresh_region_mask_coverage

            refresh_region_mask_coverage(volume)
        except Exception as exc:
            volume.region_mask_coverage = None
            volume.save(update_fields=["region_mask_coverage"])
            logger.warning(
                "Could not refresh region-mask coverage for volume %s: %s",
                volume.pk,
                exc,
            )
        _rebuild_region_derivative(volume)
    return volume


def _rebuild_region_derivative(volume: Volume) -> None:
    """Retire the region derivative built from the *previous* mask, then requeue.

    Coverage is recomputed above, but a stale pyramid would keep serving the old
    ROI to every viewer that streams it — worse than no derivative, because it
    looks correct. Dropping readiness first means the fallback slice path (which
    reads the new file) covers the gap until the rebuild lands. The image layer
    is untouched: its source did not change.
    """
    try:
        from volumes.pyramid.service import clear_pyramid

        clear_pyramid(volume, layer="region")
    except Exception:
        logger.exception(
            "Could not clear the region derivative for volume %s", volume.pk
        )
    enqueue_pyramid_build(volume, layer="region", trigger="region_mask_changed")


def infer_task_type(label_type: str, override: str | None = None) -> str:
    """Infer the task type for a volume's label state (override wins).

    One volume is one assignable unit, so the only caller that matters is
    :func:`annotation.services.create_whole_volume_task`. There is deliberately
    no helper here that turns a volume into *several* tasks.
    """
    if override:
        return override
    return LABEL_TYPE_TO_TASK_TYPE.get(label_type, LABEL_TYPE_TO_TASK_TYPE[LabelType.NONE])
