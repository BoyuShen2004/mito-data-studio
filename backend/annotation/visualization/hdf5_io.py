"""Lazy, slice-at-a-time HDF5 volume access for the viewer/editor.

Why this exists: an EM volume that arrives as ``.h5`` used to have exactly one
route into this app — transcode it to an uncompressed TIFF so ``slice_io`` could
memmap it. That costs a *second full copy* of every volume forever (and for a
sparse instance mask, far worse than a copy: ``256x2048x2048`` uint16 masks in
``nag/p10_batch1`` occupy ~3 MB compressed in HDF5 and 2.1 GB uncompressed).

HDF5 needs none of it. The property ``slice_io`` actually depends on is *"read
one plane without touching the rest of the file"*, and a chunked dataset has
that natively — the chunk B-tree is the same random-access index a TIFF's
per-page offsets provide. So the source stays where it is, in its original
format, and this module hands ``slice_io`` something that indexes like a
``(Z, Y, X)`` array.

What it deliberately does **not** do: make HDF5 *writable*. Labels are edited
through ``slice_io.open_label_volume_writable``, whose whole performance model
(and multi-worker safety) is a plain uncompressed memmap. Nothing here changes
that — an HDF5 mask is a *source* that seeds a TIFF working copy, exactly like
a compressed TIFF mask already does.

Two details are load-bearing:

* **Chunk cache.** HDF5 reads whole chunks. With ``(8, 128, 128)`` chunks, one
  z-plane of a 2048² volume touches 256 chunks and decompresses 8 planes' worth
  of data. The default 1 MB cache holds one of those 256 chunks, so scrolling
  z=0..7 would re-read and re-inflate the same chunks eight times over. We size
  the cache to hold one full plane's worth of chunks (:func:`_chunk_cache_bytes`),
  which makes the other seven planes in each chunk row free.
* **File locking off.** Sources are opened read-only and commonly live on a
  read-only mount or network filesystem (Weka) where HDF5's lock either fails
  outright or is meaningless. ``locking=False`` keeps a read-only open from
  needing write access to the file's directory.

Parity rule
-----------

An ``.h5`` volume must behave identically to the same voxels stored as
``.tif``, everywhere — same metadata, same slices on every axis, same Labels
panel, same 3D meshes, same tools. ``test_hdf5_tiff_parity.py`` registers one
volume in both formats and compares the two answers value by value, so a new
format branch fails there without anyone having to guess where to look.

The way that rule gets broken is always the same, and it is worth recognising:
**open volumes through this module or ``slice_io``, never with ``tifffile``
directly.** A direct ``tifffile.memmap`` is correct for the *working copy* —
this app always writes that as TIFF — and wrong for the volume's *official*
label, which is whatever the manager registered. Code that only ever ran
against a seeded working copy therefore looks fine in Annotate and raises
``TiffFileError`` on a public share of the same volume, which had no working
copy to seed. ``cellable_port/labels_3d.py`` shipped exactly that bug; it now
goes through :func:`slice_io.open_label_volume_readonly`.

One difference is **not** closed, deliberately: materialising a whole volume
(``np.asarray``) costs nothing extra for a TIFF, because the result still
shares the memmap's pages, but reads an HDF5 volume fully into RAM (~1.1 GB
for a 256x2048² uint8 crop). Per-slice and bounding-box reads are unaffected;
only ``track_task_*`` still asks for a whole volume at once.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# ``.he5`` is NASA-EOS HDF5; harmless to accept, it opens with the same reader.
HDF5_SUFFIXES = {".h5", ".hdf5", ".he5"}

# Dataset names tried, in order, before falling back to "the only 3-D dataset
# in the file". ``main`` is what the pipeline that produced ``nag/p10_batch1``
# writes; the rest are the common conventions from Fiji, ilastik and nnU-Net.
DEFAULT_DATASET_CANDIDATES = (
    "main",
    "data",
    "image",
    "images",
    "raw",
    "volume",
    "labels",
    "label",
    "exported_data",
    "stack",
)

# Upper bound for the per-dataset chunk cache. A plane's chunk row is ~33 MB for
# the p10 volumes; the cap keeps a pathological chunking (or many volumes open
# at once) from turning into hundreds of MB per Gunicorn worker.
MAX_CHUNK_CACHE_BYTES = 96 * 1024 * 1024
DEFAULT_CHUNK_CACHE_BYTES = 1024 * 1024  # h5py's own default, for unchunked data

# Copy granularity when seeding a TIFF working copy from an HDF5 mask. 16 planes
# of a 2048² uint16 mask is 128 MB — big enough that the copy is sequential,
# small enough that materialising the whole 2 GB volume is never required.
SEED_COPY_BLOCK = 16


class Hdf5Error(Exception):
    """Raised when a file cannot be opened as a (Z, Y, X) HDF5 volume."""


def is_hdf5_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() in HDF5_SUFFIXES


def _require_h5py():
    try:
        import h5py
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise Hdf5Error(
            "Reading .h5/.hdf5 volumes requires the 'h5py' package, which is "
            "not installed in this environment."
        ) from exc
    return h5py


def _effective_ndim(shape) -> int:
    """Dimensionality after dropping leading singleton axes.

    A ``(1, Z, Y, X)`` channel axis (common in nnU-Net exports) describes a 3-D
    volume; anything with a non-singleton extra axis does not.
    """
    dims = len(shape)
    if dims <= 3:
        return dims
    if all(s == 1 for s in shape[: dims - 3]):
        return 3
    return dims


def _volume_datasets(handle, ndim: int) -> list[str]:
    """Every numeric dataset in the file whose effective ndim is ``ndim``."""
    h5py = _require_h5py()
    found: list[str] = []

    def visit(name, obj):
        if not isinstance(obj, h5py.Dataset) or obj.dtype.kind not in "uifb":
            return
        if _effective_ndim(obj.shape) == ndim:
            found.append(name)

    handle.visititems(visit)
    return found


def _select_dataset(handle, path: Path, dataset: str = "") -> str:
    """Pick which dataset in the file *is* the volume.

    A TIFF is one image per file; an HDF5 file can hold several (raw + mask +
    thumbnails). Explicit request wins; then the conventional names; then, if
    the file contains exactly one volume-shaped dataset, that one. Anything
    else is an error that *names the candidates*, because guessing between two
    plausible volumes silently annotates the wrong data.

    3-D is preferred over 2-D throughout, so a stack sitting next to a
    single-plane thumbnail resolves to the stack rather than reading as
    ambiguous.
    """
    h5py = _require_h5py()
    if dataset:
        key = dataset.lstrip("/")
        node = handle.get(key)
        if not isinstance(node, h5py.Dataset):
            raise Hdf5Error(f"{path.name} has no dataset '{dataset}'.")
        return key

    volumes = _volume_datasets(handle, 3)
    planes = _volume_datasets(handle, 2)
    for group in (volumes, planes):
        for name in DEFAULT_DATASET_CANDIDATES:
            if name in group:
                return name
        if len(group) == 1:
            return group[0]
        if group:
            raise Hdf5Error(
                f"{path.name} contains several candidate volumes "
                f"({', '.join('/' + c for c in sorted(group)[:6])}); set "
                "MITO_HDF5_DATASET to say which one holds the data."
            )
    raise Hdf5Error(
        f"{path.name} contains no 2-D or 3-D dataset to read as a volume."
    )


def _chunk_cache_bytes(chunks: tuple[int, ...] | None, itemsize: int, shape) -> int:
    """Cache size that holds every chunk one z-plane touches (capped).

    This is the difference between scrolling z costing one inflate per plane and
    costing one inflate per plane *per chunk row* — see the module docstring.
    """
    if not chunks:
        return DEFAULT_CHUNK_CACHE_BYTES
    cz, cy, cx = chunks[-3:]
    _, sy, sx = shape[-3:]
    chunks_per_plane = -(-sy // cy) * -(-sx // cx)
    plane_row = chunks_per_plane * cz * cy * cx * itemsize
    # A little headroom so the row still fits once a neighbouring plane starts
    # loading, then clamp.
    return int(min(max(plane_row * 5 // 4, DEFAULT_CHUNK_CACHE_BYTES), MAX_CHUNK_CACHE_BYTES))


def _cache_slots(cache_bytes: int, chunks: tuple[int, ...] | None, itemsize: int) -> int:
    """Hash-table slots for the chunk cache.

    HDF5 hashes chunks into a fixed number of slots; too few and cached chunks
    evict each other regardless of how many bytes the cache is allowed. The
    rule of thumb is ~10-100x the number of chunks that fit, and a prime.
    """
    if not chunks:
        return 521
    chunk_bytes = itemsize
    for c in chunks:
        chunk_bytes *= c
    resident = max(1, cache_bytes // max(chunk_bytes, 1))
    target = resident * 20
    for prime in (521, 2053, 8209, 32771, 131101):
        if prime >= target:
            return prime
    return 131101


class Hdf5Volume:
    """A ``(Z, Y, X)``-shaped, lazily-read view over one HDF5 dataset.

    Presents the small slice of the ``np.memmap`` surface that ``slice_io`` and
    its callers actually use — ``shape``/``dtype``/``ndim``/``size``, indexing,
    ``max()``, and ``np.asarray()`` — so ``_open_volume`` can return one of
    these wherever it returns a memmap, and nothing downstream needs to know.

    Indexing returns real ``ndarray``s (HDF5 has no memory-mapped view), which
    is exactly what every caller does with the result anyway.

    The open file handle lives here: the object is cached in ``slice_io``'s
    volume LRU, and the file closes when the entry is evicted.
    """

    __slots__ = ("_handle", "_ds", "_prefix", "_shape", "path", "dataset")

    def __init__(self, handle, dataset, path: Path, name: str):
        self._handle = handle
        self._ds = dataset
        # A (1, ..., Z, Y, X) dataset is addressed as (Z, Y, X) by pinning the
        # leading singleton axes to 0 on every read.
        self._prefix = (0,) * (dataset.ndim - 3)
        self._shape = tuple(int(s) for s in dataset.shape[-3:])
        self.path = path
        self.dataset = name

    # --- array-like surface -------------------------------------------------

    @property
    def shape(self) -> tuple[int, int, int]:
        return self._shape

    @property
    def dtype(self):
        return self._ds.dtype

    @property
    def ndim(self) -> int:
        return 3

    @property
    def size(self) -> int:
        return int(np.prod(self._shape))

    @property
    def chunks(self):
        return self._ds.chunks

    def __len__(self) -> int:
        return self._shape[0]

    def __getitem__(self, key):
        if not isinstance(key, tuple):
            key = (key,)
        return self._ds[self._prefix + key]

    def __array__(self, dtype=None, copy=None):
        """Materialise the whole volume — only for the callers that genuinely
        need an in-memory array (whole-volume operations).

        Per-slice reads must go through indexing instead; ``np.asarray`` here
        reads and inflates every chunk in the file.
        """
        arr = self._ds[self._prefix] if self._prefix else self._ds[...]
        return arr.astype(dtype) if dtype is not None else arr

    def max(self) -> int | float:
        """Volume-wide maximum, read a z-block at a time.

        Used for ``label_max_id`` on a registered HDF5 mask. Scanning in blocks
        keeps peak memory at one block instead of the whole (potentially
        multi-GB uncompressed) volume.
        """
        if not self.size:
            return 0
        best = None
        for z0 in range(0, self._shape[0], SEED_COPY_BLOCK):
            block = np.asarray(self[z0 : z0 + SEED_COPY_BLOCK])
            if not block.size:
                continue
            block_max = block.max()
            best = block_max if best is None else max(best, block_max)
        return 0 if best is None else best

    # --- lifetime -----------------------------------------------------------

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except Exception:  # pragma: no cover - closing a dead handle
                pass

    def __del__(self):  # pragma: no cover - GC timing
        self.close()

    def __repr__(self) -> str:
        return f"<Hdf5Volume {self.path.name}:/{self.dataset} {self._shape} {self.dtype}>"


def _configured_dataset(dataset: str = "") -> str:
    """An explicit dataset name, else ``MITO_HDF5_DATASET``, else ``""``.

    Every entry point has to consult the setting, not just the one that opens
    voxels: an operator who sets it to disambiguate a bundled file expects
    registration to read that file's *shape* too. When only the reader honoured
    it, such a volume opened fine and still registered with no ``shape_z`` —
    and a volume with no shape can never be turned into a task.
    """
    if dataset:
        return dataset
    from django.conf import settings

    return getattr(settings, "MITO_HDF5_DATASET", "") or ""


def open_hdf5_volume(path: Path | str, dataset: str = "") -> Hdf5Volume:
    """Open ``path`` read-only and return its volume dataset as a lazy view.

    The file is opened twice on purpose: the chunk cache is fixed at open time,
    but the right size for it is a property of the dataset's chunk shape, which
    can only be read once the file is open. The first open is a header read.
    """
    h5py = _require_h5py()
    path = Path(path)
    dataset = _configured_dataset(dataset)

    try:
        with h5py.File(str(path), "r", locking=False) as probe:
            name = _select_dataset(probe, path, dataset)
            node = probe[name]
            chunks = node.chunks
            itemsize = node.dtype.itemsize
            shape = node.shape
            ndim = node.ndim
    except Hdf5Error:
        raise
    except OSError as exc:
        raise Hdf5Error(f"Could not open {path.name} as HDF5: {exc}") from exc

    if ndim < 2:
        raise Hdf5Error(
            f"{path.name}:/{name} has shape {tuple(shape)}; a volume needs at "
            "least 2 dimensions."
        )
    if ndim > 3 and any(s != 1 for s in shape[: ndim - 3]):
        raise Hdf5Error(
            f"{path.name}:/{name} has shape {tuple(shape)}; only leading "
            "singleton axes can be dropped to reach (Z, Y, X)."
        )

    cache_bytes = _chunk_cache_bytes(chunks, itemsize, shape) if ndim >= 3 else DEFAULT_CHUNK_CACHE_BYTES
    handle = h5py.File(
        str(path),
        "r",
        locking=False,
        rdcc_nbytes=cache_bytes,
        rdcc_nslots=_cache_slots(cache_bytes, chunks, itemsize),
        rdcc_w0=0.0,  # evict fully-read chunks first; nothing here is written
    )
    try:
        node = handle[name]
        if ndim == 2:
            # A single plane: small by definition, and the (1, Y, X) reshape
            # ``_open_volume`` already does for 2-D inputs is simpler than
            # teaching the wrapper a second indexing mode.
            arr = np.asarray(node[...])
            handle.close()
            return arr[np.newaxis, ...]
        return Hdf5Volume(handle, node, path, name)
    except Exception:
        handle.close()
        raise


def hdf5_shape_xyz(path: Path | str, dataset: str = "") -> tuple[int, int, int] | None:
    """``(x, y, z)`` from headers alone, or ``None`` if it can't be read.

    Matches ``core.utils.read_tiff_shape_fast``'s axis order (x, y, z), which is
    what the volume registration path stores.
    """
    h5py = _require_h5py()
    try:
        with h5py.File(str(path), "r", locking=False) as handle:
            name = _select_dataset(handle, Path(path), _configured_dataset(dataset))
            shape = handle[name].shape
    except (Hdf5Error, OSError) as exc:
        # Best-effort by contract, but the caller (registration) turns a None
        # into a volume with no shape and therefore no possible task, so leave
        # a trace of *why* rather than only of *that*.
        logger.debug("Could not read the shape of %s: %s", path, exc)
        return None
    if len(shape) < 3:
        return None
    z, y, x = (int(s) for s in shape[-3:])
    return (x, y, z)


def hdf5_voxel_size_zyx(
    path: Path | str, dataset: str = ""
) -> tuple[float, float, float] | None:
    """``(z, y, x)`` voxel size in µm from the ``element_size_um`` attribute.

    That attribute is the Fiji/ilastik HDF5 convention (and is written in
    (z, y, x) order, in µm, which is already this function's contract). Returns
    ``None`` when absent — most pipeline-written files carry no spacing at all.
    """
    h5py = _require_h5py()
    try:
        with h5py.File(str(path), "r", locking=False) as handle:
            name = _select_dataset(handle, Path(path), _configured_dataset(dataset))
            node = handle[name]
            raw = node.attrs.get("element_size_um")
            if raw is None:
                raw = handle.attrs.get("element_size_um")
            if raw is None:
                return None
            values = [float(v) for v in np.atleast_1d(np.asarray(raw)).ravel()]
    except (Hdf5Error, OSError, TypeError, ValueError):
        return None
    if len(values) != 3 or any(v <= 0 for v in values):
        return None
    return (values[0], values[1], values[2])


def copy_into_label_memmap(mm, source, *, block: int = SEED_COPY_BLOCK) -> int:
    """Copy a lazy source volume into an open label memmap in z-blocks.

    Seeding a TIFF working copy from an HDF5 mask must not go through
    ``np.asarray(source)``: the p10 masks are ~3 MB on disk but 2.1 GB
    materialised, and the naive path pays that twice (source array + memmap
    write) in every worker that opens the volume.

    Returns the highest id written, so the caller can seed
    ``slice_io.set_label_max_id`` without a second full scan.
    """
    max_id = 0
    depth = mm.shape[0]
    for z0 in range(0, depth, block):
        z1 = min(z0 + block, depth)
        chunk = np.asarray(source[z0:z1], dtype=np.uint16)
        mm[z0:z1] = chunk
        if chunk.size:
            max_id = max(max_id, int(chunk.max()))
    mm.flush()
    return max_id
