"""The ownership boundary for everything this instance writes.

Every in-app edit — the working mask, its metadata sidecar, the embedding
cache — must land under *this running instance's* ``settings.MITO_DATA_ROOT``
and nowhere else. Two distinct things must never be written:

* **Registered source images and labels.** A volume's image (and sometimes its
  official label) may be registered *by reference* to an absolute path in
  someone else's tree. Those are read-only inputs. Mutating one in place would
  corrupt data this application does not own and cannot restore.
* **Another instance's data root.** Development, the current deployment and any
  retired deployment each have their own root. Writing into the wrong one is
  silent: the save succeeds, returns 200, and the bytes land where nobody is
  looking.

The path *construction* in ``annotation/label_paths.py`` is already correct —
it returns paths relative to the data root, which resolve inside it by
definition. This module is the *enforcement* half, so that correctness stops
depending on every future caller remembering the rule. It is deliberately
cheap (one ``resolve()`` and one prefix comparison) and sits at the two write
primitives in ``annotation/visualization/slice_io.py`` that every label write
funnels through.

Why ``resolve()`` on both sides: a symlink placed inside the data root that
points outside it would otherwise pass a naive string-prefix check while
writing to the target. Resolving first collapses that, and it also normalises
``..`` segments. Non-existent paths resolve fine (the file being created does
not exist yet); only the parent directories need to be real.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings


class ExternalWriteRefused(RuntimeError):
    """A write was attempted outside this instance's ``MITO_DATA_ROOT``.

    Deliberately not a ``ValidationError``: this is never a user input problem
    and must not be rendered as a 400 for someone to retry. It means the code
    tried to write somewhere it does not own, which is a bug or a
    misconfiguration, and the write must fail loudly rather than land in the
    wrong tree.
    """


def data_root() -> Path:
    """This instance's resolved data root."""
    return Path(settings.MITO_DATA_ROOT).resolve()


def _logical(path: Path | str) -> Path:
    """Absolute, lexically normalised — ``..`` collapsed, symlinks untouched."""
    return Path(os.path.normpath(os.path.abspath(str(path))))


def is_owned(path: Path | str) -> bool:
    """True when ``path`` lies inside this instance's data root.

    Containment is judged **either** logically (``..`` collapsed, symlinks left
    alone) **or** after resolving symlinks; a path that satisfies either one is
    owned.

    Accepting the logical form matters operationally. Large microscopy trees are
    routinely placed on a second disk and symlinked into position
    (``data/webknossos -> /bigdisk/webknossos``). Resolving first would put the
    real file outside the root and refuse every save — turning a storage layout
    choice into a total annotation outage.

    That is a deliberate narrowing of what this guard claims. It is a
    **correctness** boundary — "am I writing into the right instance, and not
    onto a read-only source image?" — not a defence against someone who can
    already plant symlinks inside the data root. Both real failure modes are
    caught lexically: another deployment's root and an external source path are
    different paths, not symlink tricks, and ``..`` traversal is collapsed
    before comparison. The application itself never creates symlinks.

    The root itself counts as owned; a sibling whose name merely starts with
    the root's name (``/data-old`` vs ``/data``) does not — ``is_relative_to``
    compares path components, not characters.
    """
    root_logical = _logical(settings.MITO_DATA_ROOT)
    if _logical(path).is_relative_to(root_logical):
        return True
    try:
        return Path(path).resolve().is_relative_to(data_root())
    except (OSError, ValueError):
        # An unresolvable path (broken symlink loop, bad encoding) is by
        # definition not something we can prove we own.
        return False


def assert_owned(path: Path | str, *, what: str = "file") -> Path:
    """Return ``path`` unchanged (absolute), or raise
    :class:`ExternalWriteRefused`.

    Call this immediately before creating or opening anything for writing.
    The message names both the offending path and the expected root, because
    the failure this guards against is precisely one of mistaken instance
    identity — the operator needs to see *which* root the process actually has.
    """
    if not is_owned(path):
        raise ExternalWriteRefused(
            f"Refusing to write {what} outside this instance's data root.\n"
            f"  target:         {_logical(path)}\n"
            f"  MITO_DATA_ROOT: {data_root()}\n"
            "Registered source images/labels are read-only, and each "
            "deployment owns only its own data root."
        )
    return _logical(path)
