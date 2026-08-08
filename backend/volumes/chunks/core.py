"""The chunk read core — framework-independent by design.

No Django import anywhere in this module. ADR-010 §1 resolves doc 20's
"separate Chunk Svc process" by making the read path a module a future ASGI host
can import unchanged, rather than standing up a second deployable now. That
promise is only real if the core genuinely does not depend on the web framework,
so this file is where that is enforced.

What lives here: the chunk address, bounds and limit checking, the cached handle
lookup, and the read itself. What does not: permissions, HTTP, tokens, the ORM.

No function in this module accepts or returns a filesystem path: an address is
integers plus two names, so there is no traversal surface to defend because
there is no path in the request at all.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from .metrics import METRICS, timed

#: Refuse to assemble a block larger than this many voxels, whatever the
#: derivative's chunk shape says. A guard against a future chunk shape making a
#: single request enormous.
DEFAULT_MAX_CHUNK_VOXELS = 4 * 1024 * 1024
#: Refuse to return more than this many bytes in one response.
DEFAULT_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
#: How many zarr group handles to keep open.
DEFAULT_CACHE_ENTRIES = 32


class ChunkError(Exception):
    """A chunk request that cannot be served. Carries a machine-readable reason."""

    status = 400

    def __init__(self, message: str, *, reason: str, status: int | None = None):
        super().__init__(message)
        self.reason = reason
        if status is not None:
            self.status = status


class ChunkNotFound(ChunkError):
    status = 404


class ChunkTooLarge(ChunkError):
    status = 413


#: Layers a chunk may address. Mirrors ``volumes.pyramid.store.LAYERS``, spelled
#: again here because this module must not import Django to learn a constant.
LAYERS = ("image", "region")


@dataclass(frozen=True)
class ChunkAddress:
    """The canonical address, ADR-010 §2. Grid indices, never a path.

    ``layer`` widens the address from Phase 12's image-only form. It defaults to
    ``"image"`` so every existing caller and stored key keeps its meaning.
    """

    volume_id: int
    mag: str
    cz: int
    cy: int
    cx: int
    layer: str = "image"

    def key(self) -> str:
        """Stable identifier for logs and metrics. Contains no path."""
        return f"{self.volume_id}/{self.layer}/{self.mag}/{self.cz}.{self.cy}.{self.cx}"

    def validate(self) -> None:
        for name, value in (("cz", self.cz), ("cy", self.cy), ("cx", self.cx)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ChunkError(f"{name} must be an integer.", reason="bad_index")
            if value < 0:
                raise ChunkError(
                    f"{name} is negative ({value}).", reason="index_out_of_range"
                )
        if not self.mag or not str(self.mag).isdigit():
            raise ChunkError(
                f"mag {self.mag!r} is not a magnification name.", reason="bad_mag"
            )
        if self.layer not in LAYERS:
            raise ChunkError(
                f"layer {self.layer!r} is not a servable layer.", reason="bad_layer"
            )


@dataclass(frozen=True)
class ChunkResult:
    """A served chunk: raw bytes plus everything a client needs to read them."""

    address: ChunkAddress
    data: bytes
    shape: tuple[int, int, int]
    dtype: str
    voxel_offset: tuple[int, int, int]
    etag: str
    cache_hit: bool
    #: Set when the caller's ``If-None-Match`` already matched, so nothing was
    #: read and ``data`` is empty. Everything but ``address`` and ``etag`` is
    #: meaningless in that case.
    not_modified: bool = False

    @property
    def nbytes(self) -> int:
        return len(self.data)


class HandleCache:
    """Bounded LRU of open zarr groups, keyed by (volume id, layer, build identity).

    The layer belongs in the key because image and region are *separate stores*:
    one handle cannot answer for both, and omitting it would make the two layers
    of one volume evict — or worse, impersonate — each other.

    Invalidation is *derived*, not signalled: a rebuild changes the derivative's
    ``built_at``, so the key changes and the stale handle is simply never looked
    up again. A cache that relied on someone remembering to flush would
    eventually serve a store that had been replaced underneath it — and Phase 11
    promotes rebuilds atomically, so that would happen silently.
    """

    def __init__(self, max_entries: int = DEFAULT_CACHE_ENTRIES):
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple, object] = OrderedDict()
        self.max_entries = max_entries

    def get(self, key: tuple, factory):
        with self._lock:
            handle = self._entries.get(key)
            if handle is not None:
                self._entries.move_to_end(key)
                METRICS.cache_hit()
                return handle

        # Built outside the lock: opening a store can touch the filesystem, and
        # holding the lock across that would serialise every concurrent reader.
        METRICS.cache_miss()
        handle = factory()

        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                return existing  # another thread won; use one handle, not two
            self._entries[key] = handle
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return handle

    def invalidate(self, volume_id: int, layer: str | None = None) -> int:
        """Drop handles for a volume — one layer's, or every layer's."""
        with self._lock:
            doomed = [
                k
                for k in self._entries
                if k[0] == volume_id and (layer is None or k[1] == layer)
            ]
            for k in doomed:
                self._entries.pop(k, None)
            return len(doomed)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._entries)


#: Process-wide cache. Lazily populated, so a forked worker builds its own
#: handles rather than inheriting a parent's half-initialised state.
HANDLES = HandleCache()


def compute_etag(address: ChunkAddress, build_identity: str) -> str:
    """The chunk's validator, derived from its *identity* rather than its bytes.

    A chunk is immutable until a rebuild, and a rebuild changes
    ``build_identity`` — the same invariant :class:`HandleCache` already relies
    on for invalidation. So ``(address, build_identity)`` names exactly one
    immutable payload, and hashing the payload itself told us nothing extra
    while costing a full SHA-256 pass over every byte served, on the busiest
    endpoint in the app (20 139 of 27 811 requests in the current access log).

    Deriving it from the address also makes the validator known *before* the
    read, so a client holding the chunk can be answered ``304`` without opening
    the store at all (see ``read_chunk``'s ``if_none_match``). Note the SPA does
    not exercise that yet: ``chunkClient.fetchAndDecode`` sends
    ``cache: "no-store"`` because neither chunk URL is a complete cache key
    (the Phase 12 route omits the build identity; the signed route's key churns
    with every token refresh). Versioning the URL is the remaining half of that
    work; this side of it is ready.
    """
    identity = f"{address.key()}|{build_identity}"
    return hashlib.sha256(identity.encode()).hexdigest()[:32]


def read_chunk(
    *,
    address: ChunkAddress,
    group_factory,
    build_identity: str,
    max_voxels: int = DEFAULT_MAX_CHUNK_VOXELS,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    cache: HandleCache | None = None,
    if_none_match: str | None = None,
) -> ChunkResult:
    """Read one chunk by grid index.

    ``group_factory`` returns an opened zarr group; the caller supplies it so
    this module never imports zarr or Django. ``build_identity`` is what makes a
    rebuild invalidate the cache.

    ``if_none_match`` is the client's cached validator. When it matches, the
    returned :class:`ChunkResult` carries ``not_modified=True`` and no data, and
    the store is never touched — the caller turns that into a ``304``.
    """
    address.validate()
    etag = compute_etag(address, build_identity)
    if if_none_match is not None and _etag_matches(if_none_match, etag):
        return ChunkResult(
            address=address,
            data=b"",
            shape=(0, 0, 0),
            dtype="",
            voxel_offset=(0, 0, 0),
            etag=etag,
            cache_hit=True,
            not_modified=True,
        )

    handles = cache or HANDLES
    key = (address.volume_id, address.layer, build_identity)

    before = handles.size()
    group = handles.get(key, group_factory)
    cache_hit = handles.size() == before

    try:
        array = group[address.mag]
    except KeyError:
        raise ChunkNotFound(
            f"Magnification {address.mag!r} is not present for this volume.",
            reason="unknown_mag",
        ) from None

    shape = tuple(int(s) for s in array.shape)
    chunks = tuple(int(c) for c in array.chunks)

    origin = (
        address.cz * chunks[0],
        address.cy * chunks[1],
        address.cx * chunks[2],
    )
    if any(origin[a] >= shape[a] for a in range(3)):
        raise ChunkNotFound(
            f"Chunk {address.cz},{address.cy},{address.cx} is outside the "
            f"{address.mag!r} grid.",
            reason="chunk_out_of_range",
        )

    # Edge chunks are clipped, not padded: a client must be able to tell real
    # data from filler, and the shape travels with the response.
    extent = tuple(min(chunks[a], shape[a] - origin[a]) for a in range(3))
    voxels = extent[0] * extent[1] * extent[2]
    if voxels > max_voxels:
        raise ChunkTooLarge(
            f"Chunk covers {voxels} voxels, over the {max_voxels} limit.",
            reason="chunk_too_large",
        )

    with timed(lambda ms: None) as _t:
        block = np.ascontiguousarray(
            array[
                origin[0] : origin[0] + extent[0],
                origin[1] : origin[1] + extent[1],
                origin[2] : origin[2] + extent[2],
            ]
        )

    # Little-endian on the wire regardless of host, so a client never has to ask.
    if block.dtype.byteorder == ">":
        block = block.astype(block.dtype.newbyteorder("<"))
    data = block.tobytes()

    if len(data) > max_bytes:
        raise ChunkTooLarge(
            f"Response would be {len(data)} bytes, over the {max_bytes} limit.",
            reason="response_too_large",
        )

    return ChunkResult(
        address=address,
        data=data,
        shape=tuple(int(s) for s in block.shape),
        dtype=str(block.dtype.name),
        voxel_offset=origin,
        etag=etag,
        cache_hit=cache_hit,
    )


def _etag_matches(header: str, etag: str) -> bool:
    """RFC 9110 §13.1.2 ``If-None-Match``: ``*``, or any listed tag, weak or not.

    Intermediaries (and the tunnel in front of this deployment) are free to add
    the ``W/`` weak prefix, and a client may send several tags, so a naive
    string compare against one quoted value silently never matches.
    """
    header = header.strip()
    if header == "*":
        return True
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        if candidate.strip('"') == etag:
            return True
    return False


def grid_shape(array_shape: tuple[int, int, int], chunks: tuple[int, int, int]):
    """How many chunks along each axis — ceiling division."""
    return tuple(max(1, -(-array_shape[a] // chunks[a])) for a in range(3))


def describe_capabilities(group) -> dict:
    """Mags, shapes, chunk shape, dtype and grid — everything a client needs to
    address a chunk, and **no filesystem path**."""
    mags = []
    for name in sorted(group.array_keys(), key=lambda n: int(n) if n.isdigit() else 0):
        array = group[name]
        shape = tuple(int(s) for s in array.shape)
        chunks = tuple(int(c) for c in array.chunks)
        attrs = dict(array.attrs)
        mags.append(
            {
                "mag": name,
                "shape": list(shape),
                "chunks": list(chunks),
                "grid": list(grid_shape(shape, chunks)),
                "factors": attrs.get("factors"),
                "dtype": str(array.dtype.name),
            }
        )
    attrs = dict(group.attrs)
    return {
        "axes": attrs.get("axes", ["z", "y", "x"]),
        "dtype": attrs.get("dtype"),
        "built_at": attrs.get("built_at"),
        "layout_version": attrs.get("layout_version"),
        "layer": attrs.get("layer", "image"),
        "mags": mags,
    }


def build_identity_from(attrs: dict) -> str:
    """Cache key component: changes whenever the derivative is rebuilt."""
    return str(attrs.get("built_at") or "unknown")
