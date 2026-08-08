"""Deep links (Phase 9, P1).

Doc 19: *"Deep links (xyz, label, hard-case) — WK sharing + mito HardCase"*.
Gap matrix row 28 records mito as having only a "Hard-case token" against a
target of "Coord+state URLs", verdict **"Generalize"** — which is exactly what
this adds. The existing hard-case token mechanism is untouched.

Pure string/dict work: no Django, no database. Parsing yields a **descriptor**,
never an authorisation — resolution re-checks permissions server-side, so a link
can never grant access its holder does not already have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlencode, urlparse

from .common import ToolError

SCHEME = "mito"
KIND_VOLUME = "volume"
KIND_HARD_CASE = "hard-case"
KINDS = frozenset({KIND_VOLUME, KIND_HARD_CASE})

MAX_LINK_LENGTH = 2048
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


@dataclass(frozen=True)
class DeepLink:
    """A parsed link target. Carries no authority by itself."""

    kind: str
    volume_id: int | None = None
    token: str | None = None
    z: int | None = None
    y: int | None = None
    x: int | None = None
    label_id: int | None = None
    task_id: int | None = None

    @property
    def has_position(self) -> bool:
        return None not in (self.z, self.y, self.x)


def build(*, kind: str, volume_id: int | None = None, token: str | None = None,
          position: tuple[int, int, int] | None = None,
          label_id: int | None = None, task_id: int | None = None) -> str:
    """Encode a deep link.

    Query parameters are emitted in a fixed order, so the same target always
    produces the same string. Links get bookmarked, compared and diffed; an
    encoder whose output depends on dict ordering makes all three unreliable.
    """
    if kind not in KINDS:
        raise ToolError(f"Unknown deep-link kind {kind!r}; expected one of "
                        f"{sorted(KINDS)}.", reason="bad_kind")

    if kind == KIND_HARD_CASE:
        if not token or not _TOKEN_RE.match(token):
            raise ToolError("A hard-case link needs a valid share token.",
                            reason="bad_token")
        return f"{SCHEME}://{KIND_HARD_CASE}/{quote(token, safe='')}"

    if volume_id is None or int(volume_id) <= 0:
        raise ToolError("A volume link needs a positive volume id.",
                        reason="bad_volume_id")

    params: list[tuple[str, str]] = []
    if position is not None:
        if len(position) != 3:
            raise ToolError("Position must be (z, y, x).",
                            reason="bad_position")
        z, y, x = (int(v) for v in position)
        if min(z, y, x) < 0:
            raise ToolError("Positions must be non-negative.",
                            reason="negative_coordinate")
        params += [("z", str(z)), ("y", str(y)), ("x", str(x))]
    if label_id is not None:
        if int(label_id) < 0:
            raise ToolError("Label ids must be non-negative.",
                            reason="negative_label")
        params.append(("label", str(int(label_id))))
    if task_id is not None:
        params.append(("task", str(int(task_id))))

    link = f"{SCHEME}://{KIND_VOLUME}/{int(volume_id)}"
    if params:
        link += "?" + urlencode(params)
    if len(link) > MAX_LINK_LENGTH:
        raise ToolError(f"Deep link exceeds {MAX_LINK_LENGTH} characters.",
                        reason="link_too_long")
    return link


def parse(link: str) -> DeepLink:
    """Decode a deep link. Strict: a malformed link is rejected, not guessed at.

    Partially applying a link the user did not mean is worse than refusing it —
    they would be sent somewhere plausible but wrong, with no signal.
    """
    if not isinstance(link, str) or not link:
        raise ToolError("Deep link must be a non-empty string.",
                        reason="bad_link")
    if len(link) > MAX_LINK_LENGTH:
        raise ToolError(f"Deep link exceeds {MAX_LINK_LENGTH} characters.",
                        reason="link_too_long")

    parsed = urlparse(link)
    if parsed.scheme != SCHEME:
        raise ToolError(f"Expected scheme {SCHEME!r}, got {parsed.scheme!r}.",
                        reason="bad_scheme")
    kind = parsed.netloc
    if kind not in KINDS:
        raise ToolError(f"Unknown deep-link kind {kind!r}.", reason="bad_kind")
    path = parsed.path.lstrip("/")

    if kind == KIND_HARD_CASE:
        if not _TOKEN_RE.match(path):
            raise ToolError("Malformed hard-case share token.",
                            reason="bad_token")
        return DeepLink(kind=kind, token=path)

    if not path.isdigit():
        raise ToolError(f"Volume id must be an integer, got {path!r}.",
                        reason="bad_volume_id")

    q = dict(parse_qsl(parsed.query, keep_blank_values=False))

    def _int(name):
        if name not in q:
            return None
        raw = q[name]
        if not raw.lstrip("-").isdigit():
            raise ToolError(f"Deep-link {name!r} must be an integer, got "
                            f"{raw!r}.", reason="bad_parameter")
        value = int(raw)
        if value < 0:
            raise ToolError(f"Deep-link {name!r} must be non-negative.",
                            reason="negative_coordinate")
        return value

    z, y, x = _int("z"), _int("y"), _int("x")
    if (z is None) != (y is None) or (y is None) != (x is None):
        raise ToolError(
            "A deep-link position needs all three of z, y and x; a partial "
            "position would send the viewer somewhere the author did not mean.",
            reason="incomplete_position",
        )
    return DeepLink(kind=kind, volume_id=int(path), z=z, y=y, x=x,
                    label_id=_int("label"), task_id=_int("task"))
