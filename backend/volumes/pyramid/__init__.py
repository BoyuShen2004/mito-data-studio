"""Phase 11 — volume storage and pyramids.

``ladder`` and ``downsample`` are pure (no Django, no zarr); ``store`` owns
placement and the Zarr v3 contract; ``validate`` checks a built derivative
against its source; ``service`` is the only boundary callers use.

The on-disk format is locked by ADR-009 and Phase 12 may rely on it.
"""
