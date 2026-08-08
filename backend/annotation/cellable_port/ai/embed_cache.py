"""On-disk EfficientSAM embedding cache.

Ported *idea* from ``cellable/labelme/utils/pre_compute_tiff_sam_feature.py``
(background-computes ``embedding_dir/slice_{i}.npy`` files so scrubbing
through an already-visited slice never re-runs the encoder) — adapted from
"one file per slice index in a fixed local directory" to mito's multi-volume,
multi-model-variant web backend, where the same slice index means nothing
without also knowing *which volume*, *which axis*, and *which EfficientSAM
weight tier* produced it.

**Location**: beside the volume, under its dataset folder's
``embeddings/<variant>/`` directory — **not** a global
``MITO_DATA_ROOT/embeddings/`` silo (see ``annotation/label_paths.py`` for
the full per-volume layout). ``annotation.services._ai_embedding_cache_path``
resolves the dataset-relative ``embeddings/`` dir and the volume's
image-derived stem from ``label_paths`` and passes them in; this module stays
pure path/IO logic with no DB/model dependency (which also keeps its unit
tests independent of the ORM).

**Cache key** (encoded in the path/filename): the volume's ``embeddings``
dir + ``variant`` (``vits``/``vitt``) + the volume's image-derived stem +
``axis`` + ``index`` + the source image file's mtime. Two things make this
safe against silently poisoning accuracy: the stem disambiguates volumes that
share a dataset folder, and the mtime makes a variant swap (different path,
already part of the key) or an image replacement (new mtime) turn old entries
simply unreachable — never loaded, never mistaken for a match. There is no
cleanup of orphaned old-mtime files (this is a cache, not a store of record;
same cheap-to-regenerate-so-don't-bother tradeoff ``slice_io.py``'s in-memory
caches already make). Wiped for free by ``core.dev_data.clear_dev_data``'s
whole-data-root sweep, and now co-located with the volume it belongs to.
"""

from __future__ import annotations

import os
from pathlib import Path
from contextlib import contextmanager

import numpy as np
from django.conf import settings


def cache_path_for(
    embeddings_dir: str,
    stem: str,
    axis: str,
    index: int,
    variant: str,
    image_mtime: float,
    *,
    roi_token: str | None = None,
) -> Path:
    """Resolve the on-disk cache path for one embedding.

    ``embeddings_dir`` is the volume's dataset-relative ``embeddings/`` folder
    (``annotation.label_paths.volume_embeddings_dir_rel_path``); ``stem`` is
    the volume's image-derived mask stem
    (``label_paths.working_mask_stem``), used as a filename prefix so volumes
    sharing a dataset folder never collide.
    """
    root = Path(settings.MITO_DATA_ROOT) / embeddings_dir / variant
    name = f"{stem}_{axis}_{int(index)}_{int(image_mtime)}"
    if roi_token:
        safe = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in roi_token
        )
        name = f"{name}_{safe}"
    return root / f"{name}.npy"


def load(path: Path) -> np.ndarray | None:
    if not path.exists():
        return None
    try:
        return np.load(str(path))
    except (OSError, ValueError):
        # A half-written or corrupted cache file is just a miss, not an
        # error worth surfacing — the caller recomputes and overwrites it.
        return None


def save(path: Path, embedding: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # `np.save` appends `.npy` itself unless the given name already ends
    # with it — the tmp name must end in `.npy` too, or the file actually
    # lands at `<tmp>.npy` while `os.replace` looks for it at `<tmp>`.
    tmp = path.parent / f"{path.stem}.tmp.npy"
    np.save(str(tmp), embedding)
    os.replace(str(tmp), str(path))


@contextmanager
def exclusive_compute_lock(path: Path):
    """Serialize one expensive cache miss across Gunicorn processes."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / f"{path.name}.lock").open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
