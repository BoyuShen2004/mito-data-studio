"""Per-label lifecycle state (Proposed / Edited / Verified), ported from
``cellable/labelme/label_state.py``.

Cellable's ``LabelState``/``LabelOrigin`` enums and ``LabelMetadataStore``
are kept close to the original (same three states, same origin vocabulary,
same "MANUAL starts EDITED, everything automated starts PROPOSED" rule, same
JSON-sidecar-next-to-the-mask persistence model — ``get_sidecar_path``).
Adapted for mito's per-slice-streamed backend (Cellable holds the whole
volume in RAM, so its snapshot is a full-volume boolean mask; mito's
snapshot is the single (z, RLE) slice the label existed on the moment it was
proposed — see :func:`LabelMetadataStore.create_proposed`, and
:func:`revert` for the corresponding restore):

- Dropped ``LabelOrigin.INTERPOLATION`` (mito has no interpolation feature)
  and the merge/split parent/child bookkeeping + undo/redo stack (Cellable's
  in-memory undo already covers those; mito's per-instance state changes are
  small, explicit, server-round-trip actions, not part of the paint
  undo/redo stack).
- ``create_proposed`` optionally takes a single-slice snapshot
  (``snapshot_z``/``snapshot_shape``/``snapshot_rle``) instead of a
  full-volume one — for watershed-created labels, Cellable itself passes
  ``store_snapshots=False`` (see ``app.py``'s
  ``_registerAutoSegmentationLabels`` call for watershed), so mito doesn't
  need a 3D snapshot format at all: only AI-mask-created labels ever get a
  snapshot, and those only ever exist on the one slice they were created on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


logger = logging.getLogger(__name__)


class LabelState(Enum):
    PROPOSED = "proposed"
    EDITED = "edited"
    VERIFIED = "verified"


class LabelOrigin(Enum):
    AI = "ai"
    WATERSHED = "watershed"
    SPLIT = "split"
    MANUAL = "manual"
    TRACKING = "tracking"
    UNKNOWN = "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LabelMetadata:
    label_id: str
    state: LabelState = LabelState.PROPOSED
    origin: LabelOrigin = LabelOrigin.UNKNOWN
    created_at: str = field(default_factory=_now)
    last_modified_at: str = ""
    verified_at: str = ""
    # Single-slice snapshot — only ever set for AI-mask-created labels (see
    # module docstring for why watershed/tracking never need one).
    snapshot_z: int | None = None
    snapshot_shape: tuple[int, int] | None = None
    snapshot_rle: list[list[int]] | None = None
    notes: str = ""

    def __post_init__(self):
        if not self.last_modified_at:
            self.last_modified_at = self.created_at

    def has_snapshot(self) -> bool:
        return self.snapshot_rle is not None and self.snapshot_shape is not None

    def mark_edited(self) -> None:
        # Callers enforce the Verified geometry lock before reaching this
        # transition. This remains valid for Proposed/Edited labels and after
        # an explicit Unverify.
        self.state = LabelState.EDITED
        self.last_modified_at = _now()

    def mark_verified(self) -> None:
        self.state = LabelState.VERIFIED
        self.verified_at = _now()
        self.last_modified_at = self.verified_at

    def mark_proposed(self) -> None:
        self.state = LabelState.PROPOSED
        self.last_modified_at = _now()
        self.verified_at = ""

    def to_dict(self) -> dict:
        return {
            "label_id": self.label_id,
            "state": self.state.value,
            "origin": self.origin.value,
            "created_at": self.created_at,
            "last_modified_at": self.last_modified_at,
            "verified_at": self.verified_at,
            "snapshot_z": self.snapshot_z,
            "snapshot_shape": list(self.snapshot_shape) if self.snapshot_shape else None,
            "snapshot_rle": self.snapshot_rle,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LabelMetadata":
        state_value = str(data.get("state", "proposed")).lower()
        if state_value not in {s.value for s in LabelState}:
            state_value = LabelState.PROPOSED.value
        origin_value = str(data.get("origin", "unknown")).lower()
        if origin_value not in {o.value for o in LabelOrigin}:
            origin_value = LabelOrigin.UNKNOWN.value
        shape = data.get("snapshot_shape")
        return cls(
            label_id=str(data.get("label_id", "")),
            state=LabelState(state_value),
            origin=LabelOrigin(origin_value),
            created_at=data.get("created_at", "") or _now(),
            last_modified_at=data.get("last_modified_at", ""),
            verified_at=data.get("verified_at", ""),
            snapshot_z=data.get("snapshot_z"),
            snapshot_shape=tuple(shape) if shape else None,
            snapshot_rle=data.get("snapshot_rle"),
            notes=data.get("notes", ""),
        )


class LabelMetadataStore:
    """In-memory label-id -> :class:`LabelMetadata` map with JSON-sidecar
    persistence. Keys are string label ids (JSON object keys are always
    strings; kept as strings internally too, matching Cellable)."""

    VERSION = 1

    def __init__(self):
        self._labels: dict[str, LabelMetadata] = {}

    def __contains__(self, label_id) -> bool:
        return str(label_id) in self._labels

    def __len__(self) -> int:
        return len(self._labels)

    def get(self, label_id) -> LabelMetadata | None:
        return self._labels.get(str(label_id))

    def get_state(self, label_id) -> LabelState | None:
        meta = self.get(label_id)
        return meta.state if meta else None

    def get_or_create(self, label_id, origin: LabelOrigin = LabelOrigin.UNKNOWN) -> LabelMetadata:
        key = str(label_id)
        meta = self._labels.get(key)
        if meta is not None:
            return meta
        # Matches Cellable's get_or_create: MANUAL starts EDITED (a human
        # just drew it — already "reviewed" by construction); anything
        # automated starts PROPOSED (needs a human look before it's trusted).
        state = LabelState.EDITED if origin == LabelOrigin.MANUAL else LabelState.PROPOSED
        meta = LabelMetadata(label_id=key, state=state, origin=origin)
        self._labels[key] = meta
        return meta

    def mark_edited(self, label_id, *, default_origin: LabelOrigin = LabelOrigin.MANUAL) -> LabelMetadata:
        meta = self.get_or_create(label_id, origin=default_origin)
        meta.mark_edited()
        return meta

    def create_proposed(
        self,
        label_id,
        origin: LabelOrigin,
        *,
        snapshot_z: int | None = None,
        snapshot_shape: tuple[int, int] | None = None,
        snapshot_rle: list[list[int]] | None = None,
    ) -> LabelMetadata:
        key = str(label_id)
        meta = LabelMetadata(
            label_id=key,
            state=LabelState.PROPOSED,
            origin=origin,
            snapshot_z=snapshot_z,
            snapshot_shape=snapshot_shape,
            snapshot_rle=snapshot_rle,
        )
        self._labels[key] = meta
        return meta

    def verify(self, label_id) -> LabelMetadata:
        meta = self.get_or_create(label_id, origin=LabelOrigin.MANUAL)
        meta.mark_verified()
        return meta

    def unverify(self, label_id) -> LabelMetadata | None:
        """Returns the updated metadata on a successful VERIFIED -> EDITED
        transition, or ``None`` if the label wasn't verified (whether
        because it doesn't exist or is already in a non-verified state) —
        callers treat ``None`` as "nothing to unverify"."""
        meta = self.get(label_id)
        if meta is None or meta.state != LabelState.VERIFIED:
            return None
        meta.state = LabelState.EDITED
        meta.last_modified_at = _now()
        return meta

    def can_revert(self, label_id) -> bool:
        meta = self.get(label_id)
        return meta is not None and meta.has_snapshot()

    def revert(self, label_id) -> LabelMetadata | None:
        """Mark reverted (PROPOSED) — the caller is responsible for actually
        restoring the mask pixels from the returned metadata's snapshot."""
        meta = self.get(label_id)
        if meta is None or not meta.has_snapshot():
            return None
        meta.mark_proposed()
        return meta

    def remove(self, label_id) -> LabelMetadata | None:
        return self._labels.pop(str(label_id), None)

    def stats(self) -> dict:
        out = {"total": len(self._labels), "proposed": 0, "edited": 0, "verified": 0}
        for meta in self._labels.values():
            out[meta.state.value] += 1
        return out

    def verified_ids(self) -> set[int]:
        """Ids currently protected by the explicit Verified edit lock."""
        return {
            int(label_id)
            for label_id, metadata in self._labels.items()
            if metadata.state == LabelState.VERIFIED
        }

    # ----- Persistence -----

    def save(self, filepath) -> None:
        labels = {lid: meta.to_dict() for lid, meta in self._labels.items()}
        data = {
            "version": self.VERSION,
            "labels": labels,
            # Detect valid-JSON damage too (for example a state string changed
            # from "verified" to "proposed"). Older sidecars without this
            # field remain readable after strict structural validation.
            "labels_sha256": hashlib.sha256(
                json.dumps(labels, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "saved_at": _now(),
        }
        directory = os.path.dirname(filepath) or "."
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, delete=False
        ) as handle:
            tmp = handle.name
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        backup = f"{filepath}.bak"
        try:
            if os.path.exists(filepath):
                # The backup is the *previous* committed state, not a second
                # copy of the new state.  That makes accidental lifecycle
                # regressions recoverable instead of duplicating the damage.
                os.replace(filepath, backup)
            os.replace(tmp, filepath)
            if not os.path.exists(backup):
                try:
                    os.link(filepath, backup)
                except OSError:
                    shutil.copy2(filepath, backup)
        except Exception:
            if not os.path.exists(filepath) and os.path.exists(backup):
                shutil.copy2(backup, filepath)
            raise
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def load(self, filepath) -> bool:
        for candidate in (filepath, f"{filepath}.bak"):
            if not os.path.exists(candidate):
                continue
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("metadata root must be an object")
                raw_labels = data.get("labels")
                if not isinstance(raw_labels, dict):
                    raise ValueError("metadata labels must be an object")
                expected_hash = data.get("labels_sha256")
                if expected_hash is not None:
                    actual_hash = hashlib.sha256(
                        json.dumps(
                            raw_labels, sort_keys=True, separators=(",", ":")
                        ).encode("utf-8")
                    ).hexdigest()
                    if not isinstance(expected_hash, str) or actual_hash != expected_hash:
                        raise ValueError("metadata checksum mismatch")
                for lid, meta_dict in raw_labels.items():
                    if not isinstance(meta_dict, dict):
                        raise ValueError("label metadata must be an object")
                    if str(meta_dict.get("label_id", "")) != str(lid):
                        raise ValueError("label metadata id mismatch")
                    if str(meta_dict.get("state", "")).lower() not in {
                        state.value for state in LabelState
                    }:
                        raise ValueError("unknown label state")
                    if str(meta_dict.get("origin", "")).lower() not in {
                        origin.value for origin in LabelOrigin
                    }:
                        raise ValueError("unknown label origin")
                loaded = {
                    lid: LabelMetadata.from_dict(meta_dict)
                    for lid, meta_dict in raw_labels.items()
                }
                self._labels = loaded
                if candidate != filepath:
                    logger.warning(
                        "Recovered label lifecycle metadata from backup %s",
                        candidate,
                    )
                return True
            except (
                OSError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
                AttributeError,
            ):
                continue
        return False

    @staticmethod
    def sidecar_path(mask_filepath: str) -> str:
        base, ext = os.path.splitext(mask_filepath)
        return base + "_metadata.json"
