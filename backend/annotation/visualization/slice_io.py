"""On-demand slice IO with bounded LRU caches (Cellable memory patterns).

The web process must never load a whole EM volume into RAM. This module mirrors
Cellable's ``sliceCache`` / ``MAX_SLICE_PIXMAP_CACHE`` approach on the server:

* volumes are opened as **memory-maps** (``tifffile.memmap`` / ``np.load(mmap)``),
  so only the touched slices are paged in;
* decoded 2D slices are kept in a **bounded LRU** keyed by ``(path, mtime, axis,
  index)`` — revisiting a slice is instant, distant slices are evicted;
* open memmaps are themselves kept in a small LRU so switching volumes doesn't
  leak file handles.

Only the current slice (plus whatever neighbours the client prefetches) is ever
turned into a PNG and streamed, keeping both server RAM and client transfer
small. PNG encoding is a tiny built-in (no Pillow dependency).
"""

from __future__ import annotations

import io
import struct
import threading
import zlib
from collections import OrderedDict
from pathlib import Path

import numpy as np
from django.conf import settings
from PIL import Image

from .hdf5_io import Hdf5Error, is_hdf5_path, open_hdf5_volume
from .nifti_io import NiftiError, is_nifti_path, open_nifti_volume

# Bounded like Cellable's MAX_SLICE_PIXMAP_CACHE (256) / a few open volumes.
MAX_SLICE_CACHE = 256
MAX_OPEN_VOLUMES = 8
# Encoded-response cache is smaller per entry than the raw-array cache above,
# so it can afford to hold more: on a CPU-only HPC node, the expensive part of
# serving a slice is compression, not decoding, and this makes a revisited
# slice (very common when scrubbing back and forth) cost nothing to re-encode.
MAX_ENCODED_CACHE = 512
# Writable label memmaps stay open across requests (paint strokes reuse the
# same handle) — kept intentionally small since these are actively edited.
MAX_OPEN_LABEL_VOLUMES = 4

AXES = {"z": 0, "y": 1, "x": 2}

_slice_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
_volume_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
_range_cache: "OrderedDict[tuple, tuple[float, float]]" = OrderedDict()
_encoded_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_label_volume_cache: "OrderedDict[tuple, np.memmap]" = OrderedDict()
_label_max_cache: dict[str, int] = {}

# Every cache above is module-level *process* state shared by all of a gunicorn
# worker's request threads (this deployment runs `--threads 2`). The sequences
# below are read-modify-write, not single dict operations — `_lru_get` does
# `key in cache` and *then* `move_to_end(key)`, `_lru_put` inserts and *then*
# evicts — so without a lock a concurrent eviction between the two steps raises
# `KeyError` out of a plain cache read and 500s an ordinary slice request. Only
# the dict bookkeeping is held under this lock; decoding, PNG/JPEG encoding and
# all file IO stay outside it, so it is never contended for longer than a few
# pointer swaps. Re-entrant because `drop_file` is reachable from within other
# guarded helpers.
_cache_lock = threading.RLock()


class SliceIOError(Exception):
    pass


# --- path resolution --------------------------------------------------------

def resolve_path(location: str) -> Path:
    """Resolve a stored image/label location against ``MITO_DATA_ROOT``."""
    p = Path(location)
    if not p.is_absolute():
        p = Path(settings.MITO_DATA_ROOT) / location
    return p


# --- bounded LRU helpers ----------------------------------------------------

def _lru_get(cache: OrderedDict, key):
    with _cache_lock:
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        return None


def _lru_put(cache: OrderedDict, key, value, limit: int):
    with _cache_lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)


def clear_caches() -> None:
    with _cache_lock:
        _slice_cache.clear()
        _volume_cache.clear()
        _range_cache.clear()
        _encoded_cache.clear()
        _label_volume_cache.clear()
        _label_max_cache.clear()


def invalidate_read_caches(path: Path | str | None = None) -> None:
    """Drop the *read-side* caches (decoded slices, encoded PNG/JPEG bytes)
    for one file, so viewers see a just-written edit. Deliberately leaves open
    volume/label memmaps alone — those aren't stale (a memmap always reflects
    the file's current bytes) and re-opening them on every stroke would
    reintroduce exactly the cost this module exists to avoid.

    **Pass the written path.** Every cache key here starts with the file it
    came from; without the argument this drops *everything*, which is what
    made one annotator's slice save evict every other annotator's warm EM
    slices (and the untouched image slices of the very volume being edited —
    writing a label file cannot stale an image slice). ``None`` stays
    supported for callers that genuinely changed unknown files (tests,
    whole-data-root operations).
    """
    with _cache_lock:
        if path is None:
            _slice_cache.clear()
            _encoded_cache.clear()
            return
        target = str(path)
        for cache, key_index in ((_slice_cache, 0), (_encoded_cache, 1)):
            for key in [k for k in cache if k[key_index] == target]:
                cache.pop(key, None)


def drop_file(path: Path | str) -> None:
    """Forget everything cached about one file — decoded slices, encoded
    bytes, the open (read and writable) memmaps, and its max-id.

    Used before a whole-file rewrite (``services._save_label_volume``), which
    must release its own open handle but has no business dropping every other
    volume's caches: on a shared server that turns one annotator's Track /
    Watershed / Split into a cold cache for everybody else.
    """
    target = str(path)
    with _cache_lock:
        for key in [k for k in _slice_cache if k[0] == target]:
            _slice_cache.pop(key, None)
        for key in [k for k in _encoded_cache if k[1] == target]:
            _encoded_cache.pop(key, None)
        for key in [k for k in _volume_cache if k[0] == target]:
            _volume_cache.pop(key, None)
        for key in [k for k in _label_volume_cache if k[0] == target]:
            _label_volume_cache.pop(key, None)
        _label_max_cache.pop(target, None)


def _label_file_key(path: Path) -> tuple:
    """Return a cross-process-safe identity for a mutable label TIFF.

    Whole-volume operations replace files atomically.  A path-only mmap cache
    can therefore keep serving the unlinked inode in another Gunicorn worker.
    """
    stat = path.stat()
    return (
        str(path),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
    )


def _drop_stale_label_handles(path: Path, keep: tuple | None = None) -> None:
    target = str(path)
    with _cache_lock:
        for key in [
            key for key in _label_volume_cache if key[0] == target and key != keep
        ]:
            _label_volume_cache.pop(key, None)


def cache_stats() -> dict:
    with _cache_lock:
        return {
            "slices": len(_slice_cache),
            "volumes": len(_volume_cache),
            "encoded": len(_encoded_cache),
            "label_volumes": len(_label_volume_cache),
        }


# --- volume + slice access --------------------------------------------------

def _open_volume(path: Path) -> np.ndarray:
    """Return a (Z, Y, X) memory-mapped/array view of the volume, LRU-cached."""
    if not path.exists():
        raise SliceIOError(f"File not found: {path}")
    key = (str(path), path.stat().st_mtime)
    cached = _lru_get(_volume_cache, key)
    if cached is not None:
        return cached

    suffix = path.suffix.lower()
    arr: np.ndarray
    try:
        if suffix in {".tif", ".tiff"}:
            import tifffile

            try:
                # Source images are immutable inputs and may be mounted
                # read-only in staging/production. tifffile.memmap defaults to
                # ``r+``; requesting ``r`` explicitly prevents an accidental
                # write requirement (and makes the returned view non-writable).
                arr = tifffile.memmap(str(path), mode="r")
            except (ValueError, MemoryError):
                arr = tifffile.imread(str(path))
        elif is_hdf5_path(path):
            # HDF5 is already random-access by plane (chunk index ≙ TIFF page
            # offsets), so it is read in place rather than transcoded to a
            # second, uncompressed copy of every volume. The returned object
            # indexes like a (Z, Y, X) array but reads lazily — see
            # ``hdf5_io.Hdf5Volume``. Source-only: labels are still edited
            # through the memmap path below.
            arr = open_hdf5_volume(path)
        elif suffix == ".npy":
            arr = np.load(str(path), mmap_mode="r")
        elif is_nifti_path(path):
            arr = open_nifti_volume(path)
        else:
            raise SliceIOError(f"Unsupported volume format: {path.suffix}")
    except SliceIOError:
        raise
    except Hdf5Error as exc:
        # Already names the file and (for an ambiguous file) what to do next.
        raise SliceIOError(str(exc)) from exc
    except NiftiError as exc:
        raise SliceIOError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - format-specific failures
        raise SliceIOError(f"Could not open {path.name}: {exc}") from exc

    if arr.ndim == 2:
        arr = arr[np.newaxis, ...]
    elif arr.ndim > 3:
        arr = arr.reshape((-1,) + arr.shape[-2:])

    _lru_put(_volume_cache, key, arr, MAX_OPEN_VOLUMES)
    return arr


def _create_label_memmap(
    path: Path,
    shape: tuple[int, int, int],
    seed: np.ndarray | None = None,
) -> np.memmap:
    """Atomically create a **memory-mappable** uint16 label TIFF at ``path``.

    Writes to a sibling ``.tmp`` first, then replaces — so a crash mid-create
    cannot leave ``path`` existing-but-unreadable (the failure mode behind
    ``ValueError: image data are not memory-mappable`` on the next open).

    Refuses any target outside this instance's ``MITO_DATA_ROOT``: this and
    :func:`open_label_volume_writable` are the two primitives every label write
    funnels through, so guarding them here means a registered *source* image or
    another deployment's tree can never be overwritten, whatever the caller
    passed. See ``core/data_root.py``.
    """
    import tifffile

    from core.data_root import assert_owned

    assert_owned(path, what="label volume")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    mm_tmp = tifffile.memmap(
        str(tmp), shape=tuple(shape), dtype=np.uint16, mode="w+"
    )
    if seed is not None:
        mm_tmp[:] = np.asarray(seed, dtype=np.uint16)
        mm_tmp.flush()
    # else: fresh memmap is already zeros — don't rewrite ~GB of zeros.
    del mm_tmp
    tmp.replace(path)
    return tifffile.memmap(str(path), mode="r+")


def _quarantine_corrupt(path: Path) -> Path | None:
    """Move a corrupt/unreadable label file aside to ``<name>.corrupt.bak``
    instead of deleting it — so raw/header recovery or a filesystem-snapshot
    restore stays possible (a real incident: an erroneous delete was only
    saved by a Weka snapshot; treat destroying a working label as a last
    resort, never the default repair path). Only an *older* ``.corrupt.bak``
    is removed to make room. Returns the backup path, or ``None`` if even the
    rename failed (last-resort unlink attempted).
    """
    if not path.exists():
        return None
    bak = path.with_suffix(path.suffix + ".corrupt.bak")
    try:
        if bak.exists():
            bak.unlink()
        path.replace(bak)
        return bak
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass
        return None


def open_label_volume_readonly(path: Path):
    """A lazy, read-only ``(Z, Y, X)`` view of a label volume, whatever format
    it is stored in.

    The whole-volume *readers* (Labels summary, 3D meshes, 3D voxel preview)
    used to call ``tifffile.memmap`` themselves. That is correct only for the
    working copy, which this app always writes as TIFF — but those readers are
    also pointed at the volume's **official** label, which is whatever the
    manager registered. For a registered ``.h5`` prediction that call raised
    ``TiffFileError``, so the Labels panel and the 3D panel worked in Annotate
    (a TIFF working copy) and failed on a public share of the same volume (no
    working copy, so the ``.h5`` source). Routing them through here is what
    makes those two cases the same code path again.

    Deliberately lazy: a caller that needs the whole array asks for it, but
    the readers here crop to a bounding box or scan in z-blocks precisely so
    a multi-GB volume never has to be materialised.
    """
    return _open_volume(Path(path))


def read_label_array(path: Path) -> np.ndarray:
    """Read a whole label volume into memory as an ``int32`` array, tolerating
    a non-memmapable-but-still-readable file.

    Used by the *rare* whole-volume mutators (watershed / split / merge /
    tracking) that genuinely need a real in-memory array, unlike the hot
    per-slice path (which uses a writable memmap via
    :func:`open_label_volume_writable`). Tries ``memmap`` first, then a full
    ``imread``; if the file is unreadable by both it is quarantined
    (:func:`_quarantine_corrupt`) and a :class:`SliceIOError` is raised so the
    API surfaces a clean recoverable error instead of an uncaught 500 — the
    next editor touch rebuilds a fresh working copy at the same path.
    """
    import tifffile

    if is_hdf5_path(path) or is_nifti_path(path):
        # A registered HDF5/NIfTI label is a *source*, not a working copy: it must
        # never be quarantined or rewritten by the recovery path below (that
        # policy exists for files this app created). Read it and let a genuine
        # failure surface as SliceIOError from the caller.
        try:
            source = open_hdf5_volume(path) if is_hdf5_path(path) else open_nifti_volume(path)
            return np.asarray(source).astype(np.int32)
        except (Hdf5Error, NiftiError) as exc:
            raise SliceIOError(str(exc)) from exc

    try:
        mm = tifffile.memmap(str(path), mode="r")
        return np.asarray(mm).astype(np.int32)
    except Exception:
        pass
    try:
        return np.asarray(tifffile.imread(str(path))).astype(np.int32)
    except Exception as exc:
        with _cache_lock:
            _label_volume_cache.pop((str(path),), None)
            _label_max_cache.pop(str(path), None)
        _quarantine_corrupt(path)
        raise SliceIOError(
            "Working label file was unreadable/corrupt; it was set aside "
            "(.corrupt.bak) and will be rebuilt on the next edit."
        ) from exc


def open_label_volume_writable(path: Path, shape: tuple[int, int, int]) -> np.memmap:
    """Open (or create) a label volume as a **writable** memmap, LRU-cached.

    This is the difference between a paint stroke costing milliseconds and
    costing multiple seconds. Editing one slice must only touch that slice's
    pages on disk — reading or writing the *whole* volume (``tifffile.imread``
    / ``imwrite``) on every stroke is O(volume size), not O(slice size), and
    for a real EM label volume (gigabytes) that is an 8+ second stall per
    stroke (measured). ``mm[idx] = ...; mm.flush()`` touches only that plane.

    The handle is kept open and reused across requests (small LRU — these are
    actively-edited files, unlike the read-only volume cache) so repeated
    edits on the same task don't even pay the (cheap, ~2ms) re-open cost.

    If an existing file is corrupt, wrong-shaped, or not memory-mappable,
    it is rebuilt (salvaging voxel data via ``imread`` when possible).

    Refuses any path outside this instance's ``MITO_DATA_ROOT`` — see
    :func:`_create_label_memmap` and ``core/data_root.py``. Checked before the
    cache lookup, so a refused path can never be served from a handle opened
    earlier under different settings.
    """
    import tifffile

    from core.data_root import assert_owned

    assert_owned(path, what="label volume")
    need_recreate = not path.exists()
    seed = None
    key = _label_file_key(path) if path.exists() else None
    if key is not None:
        _drop_stale_label_handles(path, keep=key)
        cached = _lru_get(_label_volume_cache, key)
        if cached is not None and cached.shape == tuple(shape):
            return cached

    if path.exists():
        try:
            mm = tifffile.memmap(str(path), mode="r+")
            if tuple(mm.shape) != tuple(shape) or mm.size == 0:
                raise ValueError(
                    f"label file shape {getattr(mm, 'shape', None)} incompatible "
                    f"with expected {tuple(shape)}"
                )
        except Exception:
            # Corrupt / non-mappable / wrong shape — try to salvage voxels.
            need_recreate = True
            try:
                arr = np.asarray(tifffile.imread(str(path)))
                if arr.shape == tuple(shape) and arr.size > 0:
                    seed = arr
            except Exception:
                seed = None
            _drop_stale_label_handles(path)
            _label_max_cache.pop(str(path), None)
            # Never silently destroy a broken working copy — move it aside so
            # raw/header recovery or a filesystem snapshot restore is still
            # possible (see _quarantine_corrupt).
            _quarantine_corrupt(path)

    if need_recreate:
        mm = _create_label_memmap(path, shape, seed=seed)
    # else: mm already opened successfully above

    key = _label_file_key(path)
    _drop_stale_label_handles(path, keep=key)
    _lru_put(_label_volume_cache, key, mm, MAX_OPEN_LABEL_VOLUMES)
    return mm


def label_max_id(path: Path, mm: np.memmap) -> int:
    """The label volume's highest instance id, cached per process.

    ``mm.max()`` is an O(volume size) scan — cheap once, but calling it on
    every single paint stroke was the other multi-second-per-stroke cost
    alongside the old full read/write (see ``open_label_volume_writable``).
    Computed at most once per file per process; :func:`bump_label_max_id`
    updates it incrementally (O(slice size)) after that.
    """
    key = str(path)
    with _cache_lock:
        cached = _label_max_cache.get(key)
    if cached is not None:
        return cached
    # The O(volume size) scan stays outside the lock — two threads racing on a
    # cold file both compute the same number, which is cheaper than making
    # every other cache user wait behind a gigabyte-wide `max()`.
    val = int(mm.max()) if mm.size else 0
    with _cache_lock:
        return _label_max_cache.setdefault(key, val)


def set_label_max_id(path: Path, value: int) -> None:
    """Seed the cached max for a file we just wrote directly (already had
    the full array in memory — avoids a redundant memmap rescan later)."""
    with _cache_lock:
        _label_max_cache[str(path)] = value


def bump_label_max_id(path: Path, mm: np.memmap, slice_max: int) -> int:
    """Fold one freshly-written slice's max into the cached volume-wide max.

    May overestimate after an edit that erases the volume's *only* instance
    of the previous max id (it never rescans down) — harmless: this value is
    only ever used to suggest the next unused id, and skipping a retired
    number costs nothing.

    Under-estimating is *not* harmless — it hands two annotators the same "next
    free" id — so the read-modify-write is done under the cache lock rather
    than as a bare ``max()`` around two separate dict operations, which lost
    one of two concurrent bumps on the same volume.
    """
    current = label_max_id(path, mm)
    with _cache_lock:
        new_val = max(_label_max_cache.get(str(path), current), int(slice_max))
        _label_max_cache[str(path)] = new_val
    return new_val


def display_range(location: str) -> tuple[float, float]:
    """The intensity range a *raw* slice is normalised against, volume-wide.

    Raw slices are streamed once per (axis, index) and re-windowed in the
    browser, so every slice of a volume must be mapped with the *same* lo/hi —
    otherwise brightness would jump as you scroll. uint8 data uses the natural
    0–255; anything else is sampled (a few slices, 0.5/99.5 percentiles) so
    16-bit EM stacks are not crushed to black by their dtype range.
    """
    path = resolve_path(location)
    key = (str(path), path.stat().st_mtime if path.exists() else 0)
    cached = _lru_get(_range_cache, key)
    if cached is not None:
        return cached

    arr = _open_volume(path)
    if arr.dtype == np.uint8:
        rng = (0.0, 255.0)
    else:
        n = arr.shape[0]
        picks = sorted({0, n // 2, max(0, n - 1)})
        sample = np.concatenate(
            [np.asarray(arr[i], dtype=np.float32).ravel() for i in picks]
        )
        lo, hi = (float(v) for v in np.percentile(sample, [0.5, 99.5]))
        if hi <= lo:
            lo, hi = float(sample.min()), float(sample.max())
        if hi <= lo:
            hi = lo + 1.0
        rng = (lo, hi)
    _lru_put(_range_cache, key, rng, MAX_OPEN_VOLUMES)
    return rng


def volume_meta(location: str) -> dict:
    """Shape/axes/dtype for a volume, read from headers (no full load)."""
    arr = _open_volume(resolve_path(location))
    z, y, x = arr.shape
    lo, hi = display_range(location)
    return {
        "shape": {"z": int(z), "y": int(y), "x": int(x)},
        "dtype": str(arr.dtype),
        "axes": list(AXES),
        # What ``?raw=1`` normalised against, so the client can label its
        # brightness/contrast sliders in real intensity units.
        "display_range": {"lo": lo, "hi": hi},
    }


def read_slice(location: str, axis: str, index: int) -> np.ndarray:
    """Return one 2D slice along ``axis`` (``z``/``y``/``x``), LRU-cached."""
    if axis not in AXES:
        raise SliceIOError(f"Unknown axis '{axis}'. Use one of {list(AXES)}.")
    path = resolve_path(location)
    mtime = path.stat().st_mtime if path.exists() else 0
    key = (str(path), mtime, axis, int(index))
    cached = _lru_get(_slice_cache, key)
    if cached is not None:
        return cached

    arr = _open_volume(path)
    axis_i = AXES[axis]
    n = arr.shape[axis_i]
    idx = max(0, min(int(index), n - 1))
    if axis == "z":
        sl = arr[idx]
    elif axis == "y":
        sl = arr[:, idx, :]
    else:
        sl = arr[:, :, idx]
    sl = np.ascontiguousarray(sl)
    _lru_put(_slice_cache, key, sl, MAX_SLICE_CACHE)
    return sl


# --- rendering --------------------------------------------------------------

def _window_level(arr: np.ndarray, window: float | None, level: float | None) -> np.ndarray:
    """Map an image slice to uint8 using brightness/contrast (window/level)."""
    a = arr.astype(np.float32)
    if level is None or window is None or window <= 0:
        lo, hi = float(a.min()), float(a.max())
    else:
        lo, hi = level - window / 2.0, level + window / 2.0
    if hi <= lo:
        hi = lo + 1.0
    return np.clip((a - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)


def _label_color(label_id: int) -> tuple[int, int, int]:
    """Deterministic, well-spread RGB for an instance id (0 == background)."""
    if label_id <= 0:
        return (0, 0, 0)
    h = (label_id * 2654435761) & 0xFFFFFF
    return ((h >> 16) & 0xFF, (h >> 8) & 0xFF, h & 0xFF)


def colorize_labels(label_slice: np.ndarray, alpha: int = 180) -> np.ndarray:
    """Turn an instance-id slice into an RGBA overlay (background transparent)."""
    h, w = label_slice.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    for lid in np.unique(label_slice):
        if lid <= 0:
            continue
        r, g, b = _label_color(int(lid))
        mask = label_slice == lid
        rgba[mask] = (r, g, b, alpha)
    return rgba


def encode_png(arr: np.ndarray) -> bytes:
    """Encode a uint8 HxW (grayscale) or HxWx{3,4} (RGB/RGBA) array as PNG."""
    a = np.ascontiguousarray(arr, dtype=np.uint8)
    if a.ndim == 2:
        color_type, channels = 0, 1
    elif a.ndim == 3 and a.shape[2] == 3:
        color_type, channels = 2, 3
    elif a.ndim == 3 and a.shape[2] == 4:
        color_type, channels = 6, 4
    else:
        raise SliceIOError(f"Cannot encode array of shape {a.shape} as PNG")

    h, w = a.shape[:2]
    # Prepend the per-scanline filter byte (type 0 = None) in one numpy op —
    # a Python loop over rows dominated slice latency for 1k×1k slices.
    raw = np.zeros((h, w * channels + 1), dtype=np.uint8)
    raw[:, 1:] = a.reshape(h, w * channels)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def render_image_slice_png(
    location: str, axis: str, index: int, *, window=None, level=None
) -> bytes:
    """Read + window/level + PNG-encode one image slice.

    When ``window``/``level`` are both omitted, the slice is normalised
    against the volume-wide :func:`display_range` instead of its own
    min/max — the same mapping every slice of the volume uses, so a single
    fetch per slice is stable and brightness/contrast can then be adjusted
    client-side with no further network round trips. Kept for callers that
    explicitly want lossless PNG (and for back-compat); the default client
    flow now uses :func:`render_image_slice_jpeg` — much smaller and, on a
    CPU-only node, much cheaper to encode (libjpeg-turbo via Pillow).
    """
    sl = read_slice(location, axis, index)
    if window is None and level is None:
        lo, hi = display_range(location)
        mapped = np.clip((sl.astype(np.float32) - lo) / (hi - lo) * 255.0, 0, 255).astype(
            np.uint8
        )
        return encode_png(mapped)
    return encode_png(_window_level(sl, window, level))


def encode_jpeg(arr: np.ndarray, quality: int = 87) -> bytes:
    """JPEG-encode a uint8 grayscale array (Pillow / libjpeg-turbo).

    For photographic-style EM intensity data this is both smaller *and* an
    order of magnitude faster to produce than the hand-rolled PNG encoder
    above (benchmarked: ~2x smaller, ~9x faster on a 2758x2514 slice) — no
    GPU involved, libjpeg-turbo is a well-optimised C library that runs
    entirely on CPU and releases the GIL while it works. Only used for the
    intensity image; label overlays stay lossless PNG (need exact instance
    boundaries + alpha transparency).
    """
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(arr, dtype=np.uint8), mode="L").save(
        buf, format="JPEG", quality=quality
    )
    return buf.getvalue()


def render_image_slice_jpeg(location: str, axis: str, index: int, *, quality: int = 87) -> bytes:
    """Read + normalise (volume-wide display range) + JPEG-encode one slice.

    Cached by encoded bytes, not just the decoded array: re-visiting a slice
    (scrubbing back and forth is the common case) costs nothing to re-encode.
    """
    path = resolve_path(location)
    mtime = path.stat().st_mtime if path.exists() else 0
    key = ("jpeg", str(path), mtime, axis, int(index), quality)
    cached = _lru_get(_encoded_cache, key)
    if cached is not None:
        return cached
    sl = read_slice(location, axis, index)
    lo, hi = display_range(location)
    mapped = np.clip((sl.astype(np.float32) - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    out = encode_jpeg(mapped, quality=quality)
    _lru_put(_encoded_cache, key, out, MAX_ENCODED_CACHE)
    return out


def labels_touching_region(label_slice: np.ndarray, region_slice: np.ndarray) -> np.ndarray:
    """Zero every instance on ``label_slice`` that never enters the region.

    Display semantics for "Region only": an instance that overlaps the ROI *at
    all* on this plane is shown whole, and one that does not is hidden — which
    is a per-instance decision, not the per-pixel clip a mask image performs.
    Nothing here writes: the caller renders the returned copy.
    """
    if label_slice.shape != region_slice.shape:
        raise SliceIOError(
            f"Region mask slice shape {region_slice.shape} does not match label "
            f"slice shape {label_slice.shape}."
        )
    inside = np.asarray(region_slice) != 0
    keep = np.unique(label_slice[inside])
    keep = keep[keep != 0]
    if keep.size == 0:
        return np.zeros_like(label_slice)
    return np.where(np.isin(label_slice, keep), label_slice, 0)


def render_label_slice_png(
    location: str,
    axis: str,
    index: int,
    *,
    region_location: str | None = None,
) -> bytes:
    """Read + colorize + PNG-encode one label slice as an RGBA overlay.

    With ``region_location``, only instances that touch the region on this
    plane are drawn — and they are drawn *whole*, not clipped to the ROI.
    """
    path = resolve_path(location)
    mtime = path.stat().st_mtime if path.exists() else 0
    region_key: tuple = ()
    if region_location:
        region_path = resolve_path(region_location)
        region_key = (
            str(region_path),
            region_path.stat().st_mtime if region_path.exists() else 0,
        )
    key = ("label-png", str(path), mtime, axis, int(index), region_key)
    cached = _lru_get(_encoded_cache, key)
    if cached is not None:
        return cached
    sl = read_slice(location, axis, index)
    if region_location:
        sl = labels_touching_region(sl, read_slice(region_location, axis, index))
    out = encode_png(colorize_labels(sl))
    _lru_put(_encoded_cache, key, out, MAX_ENCODED_CACHE)
    return out


def render_region_mask_slice_png(location: str, axis: str, index: int) -> bytes:
    """Render the immutable reference region as a single cyan RGBA layer."""
    path = resolve_path(location)
    mtime = path.stat().st_mtime if path.exists() else 0
    key = ("region-mask-png", str(path), mtime, axis, int(index))
    cached = _lru_get(_encoded_cache, key)
    if cached is not None:
        return cached
    slice_array = read_slice(location, axis, index)
    rgba = np.zeros((*slice_array.shape, 4), dtype=np.uint8)
    rgba[slice_array != 0] = (14, 165, 233, 190)
    result = encode_png(rgba)
    _lru_put(_encoded_cache, key, result, MAX_ENCODED_CACHE)
    return result


# --- label id RLE (editor read/write) ---------------------------------------
# The in-app editor paints instance ids directly (brush/eraser), so it needs
# the *raw* ids of a slice, not a colorized overlay — and a compact encoding
# to send a whole edited slice back. Run-length encoding a label slice is
# tiny (mostly background / a handful of instances) even though the raw
# array itself (e.g. 1024x1024 int32) would not be.

def encode_label_rle(label_slice: np.ndarray) -> list[list[int]]:
    """Row-major run-length encode a 2D int label slice: ``[[id, count], ...]``."""
    flat = np.ascontiguousarray(label_slice).ravel()
    if flat.size == 0:
        return []
    change = np.flatnonzero(np.diff(flat)) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [flat.size]))
    return [[int(flat[s]), int(e - s)] for s, e in zip(starts, ends)]


def decode_label_rle(runs: list, shape: tuple[int, int]) -> np.ndarray:
    """Inverse of :func:`encode_label_rle`."""
    h, w = shape
    flat = np.empty(h * w, dtype=np.int32)
    pos = 0
    for label_id, count in runs:
        flat[pos : pos + count] = int(label_id)
        pos += int(count)
    if pos != h * w:
        raise SliceIOError(
            f"RLE covers {pos} pixels, expected {h * w} for shape {shape}."
        )
    return flat.reshape(h, w)
