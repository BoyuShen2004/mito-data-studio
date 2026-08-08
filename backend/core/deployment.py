"""Deployment identity — *which* instance is this, really?

The incident this module exists to prevent: a save succeeded, returned 200, and
wrote a working mask to disk — into a **retired deployment's** data root,
because the public hostname still routed to the old process while the operator
believed the new one was live. Nothing was broken. Every layer did exactly what
it was configured to do. The layers just did not agree, and nothing checked
that they agreed.

A deployment is not a checkout. It is the whole chain::

    public URL -> port -> process -> checkout -> database -> MITO_DATA_ROOT

Deploying code advances one link. Only verifying the chain end to end proves
which instance a user's edits actually reach — and the only way to verify it is
to ask the *public URL* who it is, rather than asking the instance you happen
to have a shell in.

:func:`identity` returns that answer in a form safe to expose: paths and
non-secret configuration only. It deliberately contains no credentials, no
``SECRET_KEY``, no tokens, and not even the database *user* — a fingerprint of
where data lands, not how to reach it.
"""

from __future__ import annotations

import hashlib
import os
import socket
from pathlib import Path

from django.conf import settings

# Never emit these, at any nesting level, whatever a future field is named.
_FORBIDDEN_SUBSTRINGS = ("password", "secret", "token", "key", "credential")

_DJANGO_FEATURE_FLAGS = (
    "FEATURE_TEAMS",
    "FEATURE_AUTO_FILL_SCHEDULER",
    "FEATURE_REVIEW_HISTORY",
    "FEATURE_DASHBOARDS",
    "FEATURE_ANNOTATION_OPS",
    "FEATURE_INTERPOLATION",
    "FEATURE_ANNOTATION_TOOLS",
    "FEATURE_VOLUME_PYRAMIDS",
    "FEATURE_CHUNK_SERVICE",
)


def _feature_identity() -> dict[str, bool]:
    """Effective server and declared build-time feature switches.

    The two Vite switches are compiled into the SPA, so Django cannot inspect
    the generated JavaScript. Release processes declare the same values in the
    service environment; reporting those declarations makes the complete
    release identity auditable without exposing any credential.
    """
    features = {
        name: bool(getattr(settings, name, False)) for name in _DJANGO_FEATURE_FLAGS
    }
    features.update(
        {
            "VITE_FEATURE_CHUNK_PULL_QUEUE": os.getenv(
                "VITE_FEATURE_CHUNK_PULL_QUEUE", "false"
            ).lower()
            in ("1", "true", "yes", "on"),
            "VITE_FEATURE_CHUNK_RENDERER": os.getenv(
                "VITE_FEATURE_CHUNK_RENDERER", "false"
            ).lower()
            in ("1", "true", "yes", "on"),
        }
    )
    return features


def _git_head(checkout: Path) -> dict[str, str | None]:
    """Branch and commit read straight from ``.git`` — no subprocess.

    Cheap enough for a health endpoint, and it works in a container where
    ``git`` may not be installed. A detached HEAD yields the commit with no
    branch, which is the normal state for a deployment pinned to a tag.
    """
    git_dir = checkout / ".git"
    head = git_dir / "HEAD"
    if not head.is_file():
        return {"branch": None, "commit": None}
    try:
        ref = head.read_text().strip()
    except OSError:
        return {"branch": None, "commit": None}
    if ref.startswith("ref: "):
        ref_path = ref[5:]
        branch = ref_path.rsplit("/", 1)[-1]
        try:
            commit = (git_dir / ref_path).read_text().strip()
        except OSError:
            commit = _packed_ref(git_dir, ref_path)
        return {"branch": branch, "commit": commit}
    return {"branch": None, "commit": ref}


def _packed_ref(git_dir: Path, ref_path: str) -> str | None:
    """Look a ref up in ``packed-refs`` — where it lives after ``git gc``."""
    packed = git_dir / "packed-refs"
    if not packed.is_file():
        return None
    try:
        for line in packed.read_text().splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1].strip() == ref_path:
                return parts[0].strip()
    except OSError:
        pass
    return None


def _database_identity() -> dict[str, object]:
    """Where rows land — engine, name, host, port. **No user, no password.**

    The name/host/port triple is what distinguishes two instances; the
    credentials are what an attacker would want. Only the former is needed to
    answer "am I talking to the right database?".
    """
    db = settings.DATABASES.get("default", {})
    engine = (db.get("ENGINE") or "").rsplit(".", 1)[-1]
    name = db.get("NAME") or ""
    if engine == "sqlite3":
        # For SQLite the file path *is* the identity, and it is a path, not a
        # secret — but show it resolved so two relative paths that differ are
        # visibly different.
        try:
            name = str(Path(name).resolve())
        except (OSError, ValueError):
            name = str(name)
    return {
        "engine": engine,
        "name": name,
        "host": db.get("HOST") or None,
        "port": str(db.get("PORT")) if db.get("PORT") else None,
    }


def _service_identity() -> dict[str, object]:
    """The service's own address and release, as **declared** by configuration.

    A WSGI application genuinely cannot observe this. Gunicorn owns the
    listening socket and Django is handed an already-accepted request, so there
    is no reliable in-process way to ask "which port am I served on?" — and
    ``request.get_host()`` reports what the *client* asked for, which is exactly
    the value under suspicion during a mis-routing incident.

    So the bind is declared in the environment (``MITO_SERVICE_BIND``) next to
    the value gunicorn is actually started with, and this reports the
    declaration. That is still useful: a declaration that disagrees with the
    expectation is a misconfiguration you can catch, and one that disagrees with
    the *real* socket is caught by the external step in DEPLOYMENT.md that
    curls the public URL. It is not, and does not claim to be, proof of the
    final Cloudflare/nginx route.
    """
    return {
        "bind": os.getenv("MITO_SERVICE_BIND") or None,
        "release": os.getenv("MITO_RELEASE") or None,
        "declared": bool(os.getenv("MITO_SERVICE_BIND")),
    }


def identity() -> dict[str, object]:
    """A safe, complete description of this instance.

    Safe to log, to return from an authenticated endpoint, and to paste into a
    cutover checklist.
    """
    checkout = Path(settings.BASE_DIR).parent.resolve()
    data_root = Path(settings.MITO_DATA_ROOT).resolve()
    database = _database_identity()

    info: dict[str, object] = {
        "checkout": str(checkout),
        "data_root": str(data_root),
        "data_root_exists": data_root.is_dir(),
        "data_root_inside_checkout": data_root.is_relative_to(checkout),
        "database": database,
        "service": _service_identity(),
        "features": _feature_identity(),
        "upgrade_profile": settings.MITO_UPGRADE_PROFILE,
        "git": _git_head(checkout),
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "debug": bool(settings.DEBUG),
        "allowed_hosts": list(settings.ALLOWED_HOSTS),
    }
    info["fingerprint"] = fingerprint(info)
    return _assert_no_secrets(info)


def fingerprint(info: dict[str, object] | None = None) -> str:
    """A short stable digest of *where this instance puts things*.

    Twelve hex characters over checkout + data root + database identity. Two
    processes sharing a fingerprint are the same deployment; two that differ
    are not, however similar their configuration looks by eye. Comparing one
    short string is something a human reliably does correctly at 2am, which a
    four-line side-by-side diff of paths is not.

    Deliberately excludes pid, hostname and git commit: restarting a service or
    deploying a new commit must **not** change a deployment's identity, or the
    check would cry wolf on every routine promotion.

    Also excludes the service bind. The bind is *declared*, not observed (see
    :func:`_service_identity`), and folding an optional declaration into the
    identity would mean forgetting to set it silently produces a different
    fingerprint — the opposite of a stable identifier. The bind is compared
    separately by ``core.checks.check_expected_identity``.
    """
    if info is None:
        info = identity()
    db = info["database"]
    assert isinstance(db, dict)
    material = "|".join(
        [
            str(info["checkout"]),
            str(info["data_root"]),
            str(db.get("engine")),
            str(db.get("name")),
            str(db.get("host")),
            str(db.get("port")),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def _assert_no_secrets(info: dict[str, object]) -> dict[str, object]:
    """Fail loudly if a field name ever suggests a credential.

    A belt-and-braces check on our own output: this dict is exposed over HTTP,
    so a future field called ``db_password`` must break a test rather than
    quietly ship. Key names only — values are paths and hostnames that may
    legitimately contain any substring.
    """

    def walk(node: object, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                if any(bad in lowered for bad in _FORBIDDEN_SUBSTRINGS):
                    raise AssertionError(
                        f"deployment.identity() would expose a secret-looking "
                        f"field: {path}{key!r}"
                    )
                walk(value, f"{path}{key}.")

    walk(info)
    return info


def describe(info: dict[str, object] | None = None) -> str:
    """Human-readable rendering, for the management command and the checklist."""
    info = info or identity()
    db = info["database"]
    assert isinstance(db, dict)
    git = info["git"]
    assert isinstance(git, dict)
    db_where = db["name"]
    if db["host"]:
        db_where = f"{db['name']} @ {db['host']}:{db['port']}"
    lines = [
        f"fingerprint     {info['fingerprint']}",
        f"checkout        {info['checkout']}",
        f"git             {git['branch'] or '(detached)'} {(git['commit'] or '')[:12]}",
        f"data root       {info['data_root']}"
        + ("" if info["data_root_exists"] else "   [MISSING]"),
        f"database        {db['engine']}  {db_where}",
        f"host / pid      {info['hostname']}  pid {info['pid']}",
        f"debug           {info['debug']}",
        f"allowed hosts   {', '.join(info['allowed_hosts']) or '(none)'}",  # type: ignore[arg-type]
    ]
    return "\n".join(lines)
