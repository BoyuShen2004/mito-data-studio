"""Per-user keyboard shortcuts for annotate tools and safe label actions.

Scope stays deliberately narrow: tool switching plus Verify and Solo, matching
the non-destructive actions in the context menu. Track-only bindings and
Save/Undo/Redo remain fixed.

The stored value is a bare letter per tool. The *modifier* is not stored,
because it is not a preference: it is Cmd on macOS and Ctrl everywhere else,
decided by the browser the person is sitting at, so the same saved profile has
to work on both. Storing "cmd+b" would pin one platform's answer into an account
that follows the user across machines.

Kept out of ``models.py`` so the validation is importable by the serializer, the
admin and the tests without dragging the model layer in with it.
"""

from __future__ import annotations

from core.choices import UserRole

# Tool switches plus the two active-label actions exposed by the canvas context
# menu. The profile page supplies the paired context-menu layout; this tuple is
# the persistence/validation contract.
ANNOTATE_SHORTCUT_TOOLS: tuple[tuple[str, str], ...] = (
    ("select", "Select"),
    ("brush", "Brush"),
    ("eraser", "Erase"),
    ("box_eraser", "Box Erase"),
    ("box_mask", "Box Mask"),
    ("point_mask", "Point Mask"),
    ("boundary", "Boundary"),
    ("seeds", "Seeds"),
    ("interpolate", "Interpolate"),
    ("flood_fill", "Flood fill"),
    ("split_3d", "Split"),
    ("merge", "Merge"),
    ("delete", "Delete"),
    ("verify", "Verify"),
    ("solo", "Solo"),
)

ANNOTATE_SHORTCUT_TOOL_IDS = tuple(tool for tool, _label in ANNOTATE_SHORTCUT_TOOLS)

# Defaults are the letters the editor already used as bare hotkeys, so an
# annotator who has learned V/B/E/... keeps the same letters when they start
# holding Cmd/Ctrl. Delete stays unbound by default rather than being given a
# destructive shortcut nobody chose.
DEFAULT_ANNOTATE_SHORTCUTS: dict[str, str] = {
    "select": "v",
    "brush": "b",
    "eraser": "e",
    "box_eraser": "r",
    "box_mask": "m",
    "point_mask": "p",
    "boundary": "o",
    "seeds": "t",
    "interpolate": "i",
    "flood_fill": "l",
    "split_3d": "c",
    "merge": "g",
    "delete": "",
    "verify": "f",
    "solo": "s",
}


def may_customize_annotate_shortcuts(role: str | None) -> bool:
    """Requesters never annotate, so they have no tools to bind."""
    return role in {UserRole.ANNOTATOR, UserRole.MANAGER}


def normalize_annotate_shortcuts(raw) -> dict[str, str]:
    """Validate a submitted map and return it in canonical form.

    Canonical means: every supported tool/action present, lower-case single
    letters, and ``""`` for "no shortcut". Raises ``ValueError`` with a message
    meant for the person editing the form — an unusable binding must fail at the
    edge, not become a key that silently does nothing.
    """
    if raw is None:
        return dict(DEFAULT_ANNOTATE_SHORTCUTS)
    if not isinstance(raw, dict):
        raise ValueError("Shortcuts must be a map of tool to letter.")

    unknown = sorted(set(raw) - set(ANNOTATE_SHORTCUT_TOOL_IDS))
    if unknown:
        raise ValueError(f"Not an annotate tool: {', '.join(unknown)}.")

    labels = dict(ANNOTATE_SHORTCUT_TOOLS)
    cleaned: dict[str, str] = {}
    claimed: dict[str, str] = {}
    for tool in ANNOTATE_SHORTCUT_TOOL_IDS:
        value = raw.get(tool, DEFAULT_ANNOTATE_SHORTCUTS[tool])
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError(f"The shortcut for {labels[tool]} must be a single letter.")
        letter = value.strip().lower()
        if letter and (len(letter) != 1 or not letter.isascii() or not letter.isalpha()):
            raise ValueError(
                f"The shortcut for {labels[tool]} must be a single letter A-Z "
                "(or empty for no shortcut)."
            )
        if letter and letter in claimed:
            # One shortcut, one tool. Two tools on the same key means whichever
            # the keydown handler happens to reach first wins, which is not a
            # preference anybody can hold.
            raise ValueError(
                f"{letter.upper()} is already used by {labels[claimed[letter]]}; "
                f"pick another letter for {labels[tool]}."
            )
        if letter:
            claimed[letter] = tool
        cleaned[tool] = letter
    return cleaned


def effective_annotate_shortcuts(stored) -> dict[str, str]:
    """What the editor should actually bind: the stored map, with defaults
    filling anything never chosen (including tools added after the profile was
    last saved). Never raises — a profile that somehow holds junk degrades to
    the defaults rather than leaving the editor with no shortcuts at all."""
    resolved = dict(DEFAULT_ANNOTATE_SHORTCUTS)
    explicit: set[str] = set()
    if isinstance(stored, dict):
        for tool in ANNOTATE_SHORTCUT_TOOL_IDS:
            value = stored.get(tool)
            if isinstance(value, str):
                letter = value.strip().lower()
                if letter == "" or (len(letter) == 1 and letter.isascii() and letter.isalpha()):
                    resolved[tool] = letter
                    explicit.add(tool)

    # A profile saved before Verify/Solo existed can explicitly contain the
    # old Flood-fill F while the newly filled Verify default also wants F.
    # Preserve what that person actually saved and leave only the newly added
    # implicit action unbound. This upgrades reads without rewriting user data.
    by_letter: dict[str, list[str]] = {}
    for tool, letter in resolved.items():
        if letter:
            by_letter.setdefault(letter, []).append(tool)
    for owners in by_letter.values():
        if len(owners) < 2:
            continue
        winner = next((tool for tool in owners if tool in explicit), owners[0])
        for tool in owners:
            if tool != winner:
                resolved[tool] = ""
    return resolved
