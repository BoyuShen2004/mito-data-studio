"""On-disk cache of SAM 2 image-encoder features, shared across workers.

The in-process LRU in ``tracking/adapters/sam2_bridge.py`` is per gunicorn
worker, and that is the problem this module exists for. The viewer warms
three slices on every slice change (the current one and its two neighbours),
those requests fan out across workers, and a worker that never saw a slice
re-runs the ~225 ms image encoder even though a sibling process encoded it
seconds ago. Scrubbing therefore stayed intermittently slow no matter how
large the per-worker cache grew.

Encoder output is a pure function of (image, model), so it can live on disk
and be read by whichever worker needs it next: 10 ms to load and upload
against 225 ms to recompute.

**Precision**: stored as float16. The features are consumed by the mask
decoder, and a float16 round-trip was measured to reproduce the fp32 mask
*exactly* (IoU 1.00000) on this data while halving both file size and load
time — 8 MiB and ~10 ms per slice rather than 16 MiB and ~20 ms.

**Location and key**: beside the volume under its dataset's
``embeddings/sam2/`` directory, keyed by the volume's image-derived stem,
axis, index and the source image's mtime — the same identity scheme, and the
same invalidation-by-unreachability, that the interactive tools have always
used. A replaced image gets a new mtime and therefore a new path, so a stale
entry is never loaded, only orphaned. This is a cache, not a store of
record: deleting the directory costs nothing but the next encode, and
``core.dev_data.clear_dev_data``'s data-root sweep clears it for free.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import zipfile

import numpy as np
from django.conf import settings

#: Subdirectory (and implicit cache-version token) for these features. Bump
#: it if the checkpoint or the stored layout ever changes, so old files
#: become unreachable rather than wrong.
VARIANT = "sam2-hiera-l"


def cache_path_for(
    embeddings_dir: str,
    stem: str,
    axis: str,
    index: int,
    image_mtime: float,
    *,
    roi_token: str | None = None,
) -> Path:
    root = Path(settings.MITO_DATA_ROOT) / embeddings_dir / VARIANT
    name = f"{stem}_{axis}_{int(index)}_{int(image_mtime)}"
    if roi_token:
        safe = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in roi_token
        )
        name = f"{name}_{safe}"
    return root / f"{name}.npz"


def load(path: Path) -> dict | None:
    """Read one cached feature set, or ``None`` for any unusable file.

    A truncated or half-written file is a miss, not an error: the caller
    re-encodes and overwrites it.
    """
    if not path.exists():
        return None
    try:
        with np.load(str(path)) as data:
            keys = set(data.files)
            if "image_embed" not in keys or "orig_hw" not in keys:
                return None
            levels = sorted(k for k in keys if k.startswith("hr"))
            return {
                "image_embed": data["image_embed"],
                "high_res_feats": [data[k] for k in levels],
                "orig_hw": [tuple(int(v) for v in hw) for hw in data["orig_hw"]],
            }
    except (OSError, ValueError, EOFError, zipfile.BadZipFile, KeyError):
        # `.npz` is a zip: a half-written or clipped file surfaces as a zip
        # error rather than an OSError, and a file written by an older layout
        # surfaces as a missing key. Both are misses.
        return None


def save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays = {"image_embed": payload["image_embed"]}
    for level, feat in enumerate(payload["high_res_feats"]):
        arrays[f"hr{level}"] = feat
    arrays["orig_hw"] = np.asarray(payload["orig_hw"], dtype=np.int64)
    # Write-then-rename: a reader must never observe a partial file, and two
    # workers racing to cache the same slice must not interleave their bytes.
    tmp = path.parent / f"{path.name}.{os.getpid()}.tmp.npz"
    try:
        np.savez(str(tmp), **arrays)
        os.replace(str(tmp), str(path))
    except OSError:
        # A full or read-only disk must not break interactive segmentation;
        # the encode already succeeded and the result is in memory.
        tmp.unlink(missing_ok=True)


@contextmanager
def exclusive_compute_lock(path: Path):
    """Serialize one expensive cache miss across gunicorn processes.

    The viewer's three-slice warm makes concurrent misses on the *same*
    slice the normal case, not a rare race: without this, three workers each
    pay a full encode for a file only one of them needed to produce.
    """
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / f"{path.name}.lock").open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class DiskFeatureStore:
    """The L2 the SAM 2 wrapper consults behind its in-process LRU."""

    def has(self, key) -> bool:
        """Whether a slice is cached, without reading 8 MiB to find out.

        Warming only needs to know that *some* worker has already paid the
        encode; the click path is what actually loads the bytes.
        """
        return Path(key).exists()

    def load(self, key):
        return load(Path(key))

    def save(self, key, payload) -> None:
        save(Path(key), payload)

    @contextmanager
    def compute_lock(self, key):
        with exclusive_compute_lock(Path(key)):
            yield
