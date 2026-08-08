"""The chunk service boundary — where permissions, storage and metrics meet.

Views call only this module. It owns the two authorization paths ADR-010 §4
defines, the flag gate, and the metric recording; `core` stays
framework-independent beneath it.

Django is the source of ACL truth here, in both paths: the authenticated path
checks permissions per request, and the token path checks them **once at
issuance** and then trusts the signature until it expires. The tradeoff is
stated in ADR-010 §4 rather than hidden — the TTL is the revocation window.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from . import core, tokens
from .metrics import METRICS, timed

__all__ = [
    "chunk_service_enabled",
    "available_layers",
    "capabilities",
    "read_chunk",
    "read_chunk_with_token",
    "issue_token",
    "issue_token_for_shared_volume",
    "capabilities_for_volume",
    "invalidate_volume",
    "ChunkServiceError",
]


class ChunkServiceError(Exception):
    """A request the service refuses. ``reason`` is machine-readable."""

    status = 400

    def __init__(self, message: str, *, reason: str, status: int | None = None):
        super().__init__(message)
        self.reason = reason
        if status is not None:
            self.status = status


class Disabled(ChunkServiceError):
    status = 503


class PermissionDenied(ChunkServiceError):
    status = 403


class NotFound(ChunkServiceError):
    status = 404


class TokenRejected(ChunkServiceError):
    status = 403


@dataclass(frozen=True)
class ServedChunk:
    result: core.ChunkResult
    volume_id: int
    build_identity: str

    @property
    def layer(self) -> str:
        return self.result.address.layer


def chunk_service_enabled() -> bool:
    """Serving needs both flags: without a derivative there is nothing to serve."""
    return bool(
        getattr(settings, "FEATURE_CHUNK_SERVICE", False)
        and getattr(settings, "FEATURE_VOLUME_PYRAMIDS", False)
    )


def _require_enabled() -> None:
    if not chunk_service_enabled():
        METRICS.rejected("disabled")
        raise Disabled(
            "The chunk service is not enabled on this instance.", reason="disabled"
        )


def _deployment() -> str:
    from core.deployment import fingerprint

    return fingerprint()


def _max_voxels() -> int:
    return int(
        getattr(settings, "MITO_CHUNK_MAX_VOXELS", core.DEFAULT_MAX_CHUNK_VOXELS)
    )


def _max_bytes() -> int:
    return int(
        getattr(settings, "MITO_CHUNK_MAX_BYTES", core.DEFAULT_MAX_RESPONSE_BYTES)
    )


# --- permissions ---------------------------------------------------------


def _volume_or_404(volume_id: int):
    from volumes.models import Volume

    try:
        return Volume.objects.select_related("project", "dataset").get(pk=volume_id)
    except Volume.DoesNotExist:
        METRICS.rejected("unknown_volume")
        raise NotFound("No such volume.", reason="unknown_volume") from None


def _may_read(user, volume) -> bool:
    """Read access to a volume's chunks follows access to its project.

    Reuses the project-membership rule the rest of the app already applies
    rather than inventing a second, subtly different one — a chunk is a view of
    a volume, and a user who may not see the volume may not see its voxels.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True

    from annotation.services import can_view_task
    from annotation.models import AnnotationTask
    from accounts.roles import is_manager

    if is_manager(user):
        return True
    project = volume.project
    if project is None:
        return False
    if getattr(project, "created_by_id", None) == user.pk:
        return True
    # Anyone with a task on this volume can already see its pixels through the
    # editor, so denying the chunk path would be inconsistent rather than safe.
    return AnnotationTask.objects.filter(
        volume=volume, assigned_to=user
    ).exists() or any(
        can_view_task(user, task)
        for task in AnnotationTask.objects.filter(volume=volume)[:1]
    )


def _may_issue_token(user, volume) -> bool:
    """Issuing is exactly read access — a token cannot widen its issuer."""
    return _may_read(user, volume)


# --- storage -------------------------------------------------------------


def _require_layer(layer) -> str:
    """Normalise a requested layer, or refuse it with a typed error."""
    name = str(layer or core.LAYERS[0])
    if name not in core.LAYERS:
        METRICS.rejected("unknown_layer")
        raise NotFound(f"Unknown layer {name!r}.", reason="unknown_layer")
    return name


def _recorded_path(volume, layer: str = "image") -> str | None:
    """The layer's derivative path as recorded at promotion time.

    Preferred over re-deriving it: derivation calls `working_mask_stem`, whose
    collision rule issues a sibling-volume query — one database round trip per
    chunk read, which would put Django CPU back on the per-chunk path.
    """
    from volumes.pyramid import store

    path = store.layer_metadata(volume, layer).get("path")
    return str(path) if path else None


def _group_factory(volume, layer: str = "image"):
    from volumes.pyramid import store

    recorded = _recorded_path(volume, layer)

    def factory():
        if recorded:
            return store.open_pyramid_at(recorded)
        return store.open_pyramid(volume, layer=layer)

    return factory


def _build_identity(volume, layer: str = "image") -> str:
    """Cache key component. Falls back to the recorded metadata when the store
    has not been opened yet, so the first read does not need to open twice."""
    from volumes.pyramid import store

    built = store.layer_metadata(volume, layer).get("built_at")
    if built:
        return str(built)

    try:
        return core.build_identity_from(store.group_attributes(volume, layer=layer))
    except Exception:
        return "unknown"


def _require_pyramid(volume, layer: str = "image") -> None:
    from volumes.pyramid import store

    if not store.layer_ready(volume, layer):
        METRICS.rejected("no_pyramid")
        raise NotFound(
            f"This volume has no validated {layer} pyramid yet.", reason="no_pyramid"
        )
    recorded = _recorded_path(volume, layer)
    if recorded:
        from annotation.visualization.slice_io import resolve_path

        exists = resolve_path(recorded).exists()
    else:
        exists = store.pyramid_location(volume, layer).path.exists()
    if not exists:
        METRICS.rejected("no_pyramid")
        raise NotFound(
            f"This volume's {layer} derivative is missing on disk.",
            reason="no_pyramid",
        )


# --- public operations ---------------------------------------------------


def available_layers(volume) -> list[str]:
    """Which layers this volume can actually serve, in mount order."""
    from volumes.pyramid import store

    return [layer for layer in core.LAYERS if store.layer_ready(volume, layer)]


def capabilities(*, volume_id: int, user, layer: str = "image") -> dict:
    """Mags, shapes, chunk grid and dtype for one layer. Never a filesystem path."""
    _require_enabled()
    layer = _require_layer(layer)
    volume = _volume_or_404(volume_id)
    if not _may_read(user, volume):
        METRICS.rejected("forbidden")
        raise PermissionDenied("You do not have access to this volume.", reason="forbidden")
    return capabilities_for_volume(volume=volume, layer=layer)


def capabilities_for_volume(*, volume, layer: str = "image") -> dict:
    """:func:`capabilities` with the ACL already decided by the caller.

    Split out so the public-share path can answer for a volume a revocable
    share has *already* authorized without inventing a second description of
    the same pyramid. Callers of this function own the access decision; every
    other check (flag, layer, derivative present) still happens here.
    """
    _require_enabled()
    layer = _require_layer(layer)
    _require_pyramid(volume, layer)

    from volumes.pyramid import store

    group = store.open_pyramid(volume, layer=layer)
    described = core.describe_capabilities(group)
    described["volume_id"] = volume.pk
    described["layer"] = layer
    # What else this volume offers, so a client mounts the ROI without probing
    # for a 404 first.
    described["layers"] = available_layers(volume)
    # Phase 13's browser cache must know this *before* a read. An ETag is only
    # learned after fetching and therefore cannot prevent a rebuilt derivative
    # from hitting a stale decoded-memory entry.
    described["build_identity"] = _build_identity(volume, layer)
    return described


def _serve(
    volume, address: core.ChunkAddress, if_none_match: str | None = None
) -> tuple[core.ChunkResult, str]:
    build_identity = _build_identity(volume, address.layer)
    try:
        with timed(lambda ms: None) as t:
            result = core.read_chunk(
                address=address,
                group_factory=_group_factory(volume, address.layer),
                build_identity=build_identity,
                max_voxels=_max_voxels(),
                max_bytes=_max_bytes(),
                if_none_match=if_none_match,
            )
    except core.ChunkError as exc:
        METRICS.rejected(exc.reason)
        raise ChunkServiceError(str(exc), reason=exc.reason, status=exc.status) from exc

    # A revalidation served nothing, so folding its 0 bytes into the fetch
    # histogram would quietly deflate the throughput numbers this endpoint is
    # judged by.
    if not result.not_modified:
        METRICS.observe_fetch(t.ms, nbytes=result.nbytes, layer=address.layer)
    return result, build_identity


def read_chunk(
    *,
    volume_id: int,
    mag: str,
    cz: int,
    cy: int,
    cx: int,
    user,
    layer: str = "image",
    if_none_match: str | None = None,
) -> ServedChunk:
    """Authenticated path — permissions checked on every request."""
    _require_enabled()
    layer = _require_layer(layer)
    volume = _volume_or_404(volume_id)
    if not _may_read(user, volume):
        METRICS.rejected("forbidden")
        raise PermissionDenied("You do not have access to this volume.", reason="forbidden")
    _require_pyramid(volume, layer)

    address = core.ChunkAddress(
        volume_id=volume.pk, mag=str(mag), cz=cz, cy=cy, cx=cx, layer=layer
    )
    result, build_identity = _serve(volume, address, if_none_match)
    return ServedChunk(
        result=result, volume_id=volume.pk, build_identity=build_identity
    )


def issue_token(
    *,
    volume_id: int,
    user,
    mags=None,
    ttl_seconds: int | None = None,
    scope=None,
    layers=None,
) -> dict:
    """Issue a short-lived read token after a full permission check.

    A token names the layers it may read. Read access is per volume, so this is
    not an extra permission — it is the same containment as ``mags``: a token
    minted for the ROI cannot be replayed against the image.
    """
    _require_enabled()
    volume = _volume_or_404(volume_id)
    if not _may_issue_token(user, volume):
        METRICS.rejected("forbidden")
        raise PermissionDenied(
            "You may not issue chunk tokens for this volume.", reason="forbidden"
        )

    return _mint_token(
        volume=volume,
        user_id=user.pk,
        mags=mags,
        ttl_seconds=ttl_seconds,
        scope=scope,
        layers=layers,
    )


#: Anonymous issuer id recorded in a share-minted token's audit claims. Tokens
#: are per volume, so this is a label for the log, never an authorization
#: input — nothing downstream resolves it to a user.
SHARE_ISSUER_ID = 0

#: Share-minted tokens are deliberately shorter-lived than the authenticated
#: default (300s). A chunk token is verified by signature alone, so its TTL is
#: the window in which a *revoked* share can still read chunks — the same
#: tradeoff ADR-010 §4 states for user tokens, but shares are revoked far more
#: often than accounts are disabled, so the window is tightened here.
SHARE_TOKEN_TTL_SECONDS = 120


def issue_token_for_shared_volume(
    *, volume, mags=None, ttl_seconds: int | None = None, layers=None, scope=None
) -> dict:
    """Mint a read token for a volume a public share already authorized.

    The caller (a share view) has resolved the share, rejected a revoked one,
    and confirmed this volume is inside the share's scope — the same gate the
    share's slice/label endpoints pass before serving the very same voxels.
    This adds no reach: the token names one volume and the layers asked for, so
    it cannot be replayed against a volume outside the share, and it carries no
    user identity to escalate.
    """
    return _mint_token(
        volume=volume,
        user_id=SHARE_ISSUER_ID,
        mags=mags,
        ttl_seconds=SHARE_TOKEN_TTL_SECONDS if ttl_seconds is None else ttl_seconds,
        scope=scope,
        layers=layers,
    )


def _mint_token(
    *, volume, user_id: int, mags, ttl_seconds, scope, layers
) -> dict:
    """Shared tail of both issue paths — everything after the access decision."""
    granted = [_require_layer(name) for name in (layers or ["image"])]
    for name in granted:
        _require_pyramid(volume, name)

    available = [
        entry["mag"]
        for entry in capabilities_for_volume(volume=volume, layer=granted[0])["mags"]
    ]
    requested = [str(m) for m in (mags or available)]
    unknown = [m for m in requested if m not in available]
    if unknown:
        raise ChunkServiceError(
            f"Unknown magnification(s): {', '.join(unknown)}.", reason="unknown_mag"
        )

    token = tokens.issue(
        user_id=user_id,
        volume_id=volume.pk,
        mags=requested,
        deployment=_deployment(),
        ttl_seconds=ttl_seconds,
        scope=scope,
        layers=granted,
    )
    verified = tokens.verify(token, deployment=_deployment())
    return {
        "token": token,
        "volume_id": volume.pk,
        "mags": requested,
        "layers": granted,
        "expires_at": verified.expires_at,
        "issued_at": verified.issued_at,
    }


def read_chunk_with_token(
    *,
    token: str,
    mag: str,
    cz: int,
    cy: int,
    cx: int,
    layer: str = "image",
    if_none_match: str | None = None,
) -> ServedChunk:
    """Token path — verifies a signature and reads. **No database ACL query.**

    The only ORM touch is loading the volume row the token names, which is a
    primary-key fetch, not a permission traversal. That is what keeps Django CPU
    off the per-chunk path (ADR-010 §1).
    """
    _require_enabled()
    layer = _require_layer(layer)

    with timed(METRICS.observe_token_verify):
        try:
            claims = tokens.verify(token, deployment=_deployment())
            tokens.authorize(
                claims,
                volume_id=claims.volume_id,
                mag=mag,
                cz=cz,
                cy=cy,
                cx=cx,
                layer=layer,
            )
        except tokens.TokenError as exc:
            METRICS.rejected(f"token_{exc.reason}")
            # Coarse message on purpose: a verifier that explains exactly why a
            # forged token failed is a forgery oracle.
            raise TokenRejected(
                "Chunk token was not accepted.", reason="token_rejected"
            ) from exc

    volume = _volume_or_404(claims.volume_id)
    _require_pyramid(volume, layer)

    address = core.ChunkAddress(
        volume_id=volume.pk, mag=str(mag), cz=cz, cy=cy, cx=cx, layer=layer
    )
    result, build_identity = _serve(volume, address, if_none_match)
    return ServedChunk(
        result=result, volume_id=volume.pk, build_identity=build_identity
    )


def invalidate_volume(volume_id: int, *, layer: str | None = None) -> int:
    """Drop cached handles for a volume — called after a rebuild replaces one.

    A region rebuild leaves the image handle alone: the two stores are
    independent, and dropping both would make every image read after an ROI
    rebuild re-open its store for nothing.
    """
    return core.HANDLES.invalidate(int(volume_id), layer)


def metrics_snapshot() -> dict:
    return METRICS.snapshot()
