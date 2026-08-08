"""Signed chunk tokens (ADR-010 §4).

Doc 20: *"Chunk service validates signed token: `user, volume_id, layers[], exp,
read|write`. Django remains source of ACL truth."*

A token is a **cached authorization decision with an expiry**, never an
independent grant. Django issues it after a full permission check; the read path
then verifies a signature and some claims and touches no database, which is what
makes doc 20's *"Django CPU not proportional to chunk QPS"* achievable.

Signing uses ``django.core.signing`` — HMAC-SHA256 over a key derived from
``SECRET_KEY`` with a **distinct salt**, so a chunk token can never be confused
with a session or password-reset token even if one were replayed into the wrong
verifier. No hand-rolled crypto, no new dependency, and constant-time comparison
comes from the library.

Read-only by construction: there is no write action in the schema, so a "write
token" is not something that can be minted and then rejected — it cannot be
expressed.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core import signing

#: Bump when the claim set changes in a way an older verifier would misread.
#: v2 adds ``lay`` (layers). A v1 token carried no layer and would therefore be
#: read as "image only" — correct, but it is cheaper to expire the handful of
#: tokens minted in the last five minutes than to keep two verifiers.
TOKEN_SCHEMA_VERSION = 2

#: Namespacing salt. Distinct from every other signed payload in the project.
TOKEN_SALT = "mito.chunks.v1"

#: The only action a token can carry.
ACTION_READ = "r"

#: Layers a token may name. A token that named no layer would have to mean
#: "all", which is the opposite of how every other claim here works.
LAYERS = ("image", "region")
DEFAULT_LAYERS = ("image",)

DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 3600


class TokenError(Exception):
    """A token that cannot be accepted. ``reason`` is machine-readable."""

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ChunkScope:
    """Optional bound on which chunks a token may read."""

    cz0: int
    cy0: int
    cx0: int
    cz1: int
    cy1: int
    cx1: int

    def contains(self, cz: int, cy: int, cx: int) -> bool:
        return (
            self.cz0 <= cz < self.cz1
            and self.cy0 <= cy < self.cy1
            and self.cx0 <= cx < self.cx1
        )

    def as_list(self) -> list[int]:
        return [self.cz0, self.cy0, self.cx0, self.cz1, self.cy1, self.cx1]

    @classmethod
    def from_list(cls, values) -> "ChunkScope":
        if not isinstance(values, (list, tuple)) or len(values) != 6:
            raise TokenError("Malformed scope.", reason="malformed")
        return cls(*(int(v) for v in values))


@dataclass(frozen=True)
class ChunkToken:
    """Verified claims."""

    schema: int
    key_version: str
    deployment: str
    user_id: int
    volume_id: int
    mags: tuple[str, ...]
    action: str
    issued_at: int
    expires_at: int
    nonce: str
    scope: ChunkScope | None = None
    layers: tuple[str, ...] = DEFAULT_LAYERS

    def audit(self) -> dict:
        """Safe to log: identifiers and times, never the signature."""
        return {
            "user_id": self.user_id,
            "volume_id": self.volume_id,
            "mags": list(self.mags),
            "layers": list(self.layers),
            "key_version": self.key_version,
            "nonce": self.nonce,
            "expires_at": self.expires_at,
        }


def key_version() -> str:
    """Current signing key version. Rotating this invalidates every token."""
    return str(getattr(settings, "MITO_CHUNK_TOKEN_KEY_VERSION", "1"))


def default_ttl() -> int:
    ttl = int(getattr(settings, "MITO_CHUNK_TOKEN_TTL_SECONDS", DEFAULT_TTL_SECONDS))
    return max(1, min(ttl, MAX_TTL_SECONDS))


def _salt(version: str) -> str:
    """Salt carries the key version, so rotating it changes the derived key."""
    return f"{TOKEN_SALT}.k{version}"


def issue(
    *,
    user_id: int,
    volume_id: int,
    mags,
    deployment: str,
    ttl_seconds: int | None = None,
    scope: ChunkScope | None = None,
    layers=None,
    now: int | None = None,
) -> str:
    """Mint a read-only token. The caller must already have checked permissions.

    This function does **not** check anything about the user: it is the last
    step of an authorization decision, not the decision. Callers live in the
    service layer, where the ACL actually is.
    """
    issued = int(now if now is not None else time.time())
    ttl = default_ttl() if ttl_seconds is None else max(1, min(int(ttl_seconds), MAX_TTL_SECONDS))
    mags = tuple(str(m) for m in mags)
    if not mags:
        raise TokenError("A token must allow at least one mag.", reason="no_mags")
    granted = tuple(str(name) for name in (layers or DEFAULT_LAYERS))
    if not granted:
        raise TokenError("A token must allow at least one layer.", reason="no_layers")
    unknown = [name for name in granted if name not in LAYERS]
    if unknown:
        raise TokenError(
            f"Unknown layer(s): {', '.join(unknown)}.", reason="bad_layer"
        )

    payload = {
        "v": TOKEN_SCHEMA_VERSION,
        "k": key_version(),
        "d": deployment,
        "u": int(user_id),
        "vol": int(volume_id),
        "mags": list(mags),
        "lay": list(granted),
        "act": ACTION_READ,
        "iat": issued,
        "exp": issued + ttl,
        "n": uuid.uuid4().hex[:12],
    }
    if scope is not None:
        payload["scope"] = scope.as_list()

    return signing.dumps(payload, salt=_salt(key_version()), compress=True)


def verify(
    token: str,
    *,
    deployment: str,
    now: int | None = None,
    leeway_seconds: int = 0,
) -> ChunkToken:
    """Verify a token's signature and claims, or raise :class:`TokenError`.

    Every failure carries a distinct ``reason`` for metrics, but the *message*
    returned to a client is deliberately coarse at the HTTP layer — a verifier
    that explains precisely why a forged token failed is a forgery oracle.
    """
    if not token or not isinstance(token, str):
        raise TokenError("No token supplied.", reason="missing")
    if len(token) > 4096:
        raise TokenError("Token is implausibly long.", reason="malformed")

    version = key_version()
    try:
        payload = signing.loads(token, salt=_salt(version))
    except signing.BadSignature:
        # Covers tampering, a different key version, and a different SECRET_KEY
        # (hence a different deployment). All are "not signed for us".
        raise TokenError("Token signature is not valid here.", reason="bad_signature") from None

    if not isinstance(payload, dict):
        raise TokenError("Malformed token payload.", reason="malformed")
    if payload.get("v") != TOKEN_SCHEMA_VERSION:
        raise TokenError(
            f"Unknown token schema {payload.get('v')!r}.", reason="bad_schema"
        )
    if str(payload.get("k")) != version:
        raise TokenError("Token was signed with a retired key.", reason="retired_key")
    if payload.get("act") != ACTION_READ:
        # Unreachable via `issue`, which cannot express another action; checked
        # anyway so a future schema change cannot quietly admit writes.
        raise TokenError("Only read tokens are accepted.", reason="bad_action")
    if str(payload.get("d")) != str(deployment):
        raise TokenError(
            "Token was issued for a different deployment.", reason="wrong_deployment"
        )

    try:
        issued_at = int(payload["iat"])
        expires_at = int(payload["exp"])
        user_id = int(payload["u"])
        volume_id = int(payload["vol"])
        mags = tuple(str(m) for m in payload["mags"])
        layers = tuple(str(name) for name in payload["lay"])
    except (KeyError, TypeError, ValueError):
        raise TokenError("Malformed token claims.", reason="malformed") from None
    if not layers or any(name not in LAYERS for name in layers):
        raise TokenError("Malformed token layers.", reason="bad_layer")

    current = int(now if now is not None else time.time())
    if current + leeway_seconds < issued_at:
        raise TokenError("Token is not valid yet.", reason="not_yet_valid")
    if current - leeway_seconds >= expires_at:
        raise TokenError("Token has expired.", reason="expired")

    scope = None
    if "scope" in payload:
        scope = ChunkScope.from_list(payload["scope"])

    return ChunkToken(
        schema=TOKEN_SCHEMA_VERSION,
        key_version=version,
        deployment=str(payload["d"]),
        user_id=user_id,
        volume_id=volume_id,
        mags=mags,
        action=ACTION_READ,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=str(payload.get("n", "")),
        scope=scope,
        layers=layers,
    )


def authorize(
    token: ChunkToken,
    *,
    volume_id: int,
    mag: str,
    cz: int,
    cy: int,
    cx: int,
    layer: str = "image",
) -> None:
    """Check a verified token actually permits *this* chunk."""
    if token.volume_id != int(volume_id):
        raise TokenError("Token is not for this volume.", reason="wrong_volume")
    if str(layer) not in token.layers:
        raise TokenError("Token does not permit this layer.", reason="wrong_layer")
    if str(mag) not in token.mags:
        raise TokenError(
            "Token does not permit this magnification.", reason="wrong_mag"
        )
    if token.scope is not None and not token.scope.contains(cz, cy, cx):
        raise TokenError("Chunk is outside the token's scope.", reason="out_of_scope")
