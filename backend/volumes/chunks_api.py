"""Phase 12 HTTP surface — the chunk/datastore service.

Thin by design: authenticate, parse integers, call one service function, map a
typed error to a status code. Everything that decides *what happens* lives in
``volumes.chunks.service``, so the same read is drivable from a benchmark or a
future ASGI host without DRF in the way (ADR-010 §1).

    GET  /api/volumes/<pk>/chunks/capabilities/            mags, grid, dtype
    GET  /api/volumes/<pk>/chunks/<mag>/<cz>/<cy>/<cx>/    authenticated read
    POST /api/volumes/<pk>/chunks/token/                   issue a signed token
    GET  /api/chunks/signed/<mag>/<cz>/<cy>/<cx>/          token read (?t=…)
    GET  /api/chunks/metrics/                              chunk-service signals

Every read route takes an optional ``?layer=image|region`` (default ``image``),
rather than a parallel set of ``/region/`` routes: the layer is one more
coordinate of the address, exactly like the mag, and duplicating five routes to
say so would double the surface that has to stay authorized identically.

Routes are registered unconditionally and report 503 when the flag is off, per
the Phase 3/6 convention — a route that vanishes with a flag makes a
misconfiguration look like a 404 typo. They do **not** appear merely because
pyramid files exist on disk.
"""

from __future__ import annotations

from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from volumes.chunks import service


def _error(exc: service.ChunkServiceError) -> Response:
    return Response(
        {"detail": str(exc), "reason": exc.reason}, status=exc.status
    )


def _layer(request) -> str:
    """The requested layer. Absent means the image, as it always did."""
    return str(request.GET.get("layer") or "image")


def _if_none_match(request) -> str | None:
    """The client's cached chunk validator, if it sent one."""
    return request.META.get("HTTP_IF_NONE_MATCH") or None


def _chunk_response(served: service.ServedChunk) -> HttpResponse:
    """Raw little-endian bytes plus metadata headers (ADR-010 §3).

    Metadata travels in headers rather than a wrapper so a client wanting pixels
    does not parse a container to find them. No filesystem path appears in any
    header, ever.

    A revalidated chunk short-circuits to ``304`` carrying only the validator
    and caching policy: per RFC 9110 §15.4.5 a 304 must not carry a body, and
    the shape/dtype/offset headers describe a payload that is not being sent.
    """
    result = served.result
    if result.not_modified:
        response = HttpResponse(status=304)
        response["ETag"] = f'"{result.etag}"'
        response["Cache-Control"] = "private, max-age=300, immutable"
        return response
    response = HttpResponse(result.data, content_type="application/octet-stream")
    response["X-Mito-Shape"] = ",".join(str(s) for s in result.shape)
    response["X-Mito-Dtype"] = result.dtype
    response["X-Mito-Byte-Order"] = "little"
    response["X-Mito-Layer"] = result.address.layer
    response["X-Mito-Mag"] = result.address.mag
    response["X-Mito-Chunk"] = (
        f"{result.address.cz},{result.address.cy},{result.address.cx}"
    )
    response["X-Mito-Voxel-Offset"] = ",".join(str(v) for v in result.voxel_offset)
    response["X-Mito-Build-Identity"] = served.build_identity
    response["ETag"] = f'"{result.etag}"'
    # Chunks are immutable until a rebuild, and a rebuild changes the ETag.
    response["Cache-Control"] = "private, max-age=300, immutable"
    return response


class VolumeChunkCapabilitiesView(APIView):
    """``GET`` the mags, shapes, chunk grid and dtype needed to address a chunk."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            return Response(
                service.capabilities(
                    volume_id=int(pk), user=request.user, layer=_layer(request)
                )
            )
        except service.ChunkServiceError as exc:
            return _error(exc)


class VolumeChunkReadView(APIView):
    """``GET`` one chunk, with a full permission check on every request."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk, mag, cz, cy, cx):
        try:
            served = service.read_chunk(
                volume_id=int(pk),
                mag=str(mag),
                cz=int(cz),
                cy=int(cy),
                cx=int(cx),
                user=request.user,
                layer=_layer(request),
                if_none_match=_if_none_match(request),
            )
        except service.ChunkServiceError as exc:
            return _error(exc)
        return _chunk_response(served)


class VolumeChunkTokenView(APIView):
    """``POST`` to mint a short-lived read token for this volume.

    Body (all optional): ``{"mags": ["2","4"], "ttl_seconds": 300,
    "scope": [cz0,cy0,cx0,cz1,cy1,cx1], "layers": ["image"]}``.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from volumes.chunks.tokens import ChunkScope, TokenError

        raw_scope = request.data.get("scope")
        scope = None
        if raw_scope is not None:
            try:
                scope = ChunkScope.from_list(raw_scope)
            except TokenError as exc:
                return Response(
                    {"detail": str(exc), "reason": exc.reason}, status=400
                )

        ttl = request.data.get("ttl_seconds")
        layers = request.data.get("layers")
        if layers is not None and (
            not isinstance(layers, list) or not all(isinstance(n, str) for n in layers)
        ):
            return Response(
                {"detail": "layers must be a list of names.", "reason": "bad_layer"},
                status=400,
            )
        try:
            issued = service.issue_token(
                volume_id=int(pk),
                user=request.user,
                mags=request.data.get("mags"),
                ttl_seconds=int(ttl) if ttl is not None else None,
                scope=scope,
                layers=layers,
            )
        except service.ChunkServiceError as exc:
            return _error(exc)
        except (TypeError, ValueError):
            return Response(
                {"detail": "ttl_seconds must be an integer.", "reason": "bad_ttl"},
                status=400,
            )
        return Response(issued)


class SignedChunkReadView(APIView):
    """``GET`` a chunk with a signed token — the high-QPS path.

    Deliberately **unauthenticated at the DRF layer**: the token *is* the
    credential, and requiring a session as well would defeat the purpose of
    having one. The volume is taken from the signed claims, never from the URL,
    so a token cannot be pointed at a different volume.
    """

    permission_classes = []  # the token is the credential
    authentication_classes = []

    def get(self, request, mag, cz, cy, cx):
        token = request.GET.get("t") or request.headers.get("X-Mito-Chunk-Token", "")
        try:
            served = service.read_chunk_with_token(
                token=token,
                mag=str(mag),
                cz=int(cz),
                cy=int(cy),
                cx=int(cx),
                layer=_layer(request),
                if_none_match=_if_none_match(request),
            )
        except service.ChunkServiceError as exc:
            return _error(exc)
        return _chunk_response(served)


class ChunkMetricsView(APIView):
    """``GET`` the chunk-service signals — the other half of the Phase 12 gate.

    Authenticated: latency and cache behaviour are operational detail with no
    reason to be anonymous. Contains counters and quantiles only — no
    identifiers, no paths, no secrets.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "enabled": service.chunk_service_enabled(),
                **service.metrics_snapshot(),
            }
        )
