"""Phase 12 — the chunk/datastore service.

``core`` is framework-independent (no Django) so a future ASGI host can import
it unchanged; ``cache`` holds zarr handles keyed by pyramid build identity;
``tokens`` mints and verifies signed chunk tokens; ``metrics`` records the
signals doc 23 names; ``service`` is the boundary the HTTP layer calls.

The on-disk contract being served is locked by ADR-009; the authorization and
response contract by ADR-010.
"""
