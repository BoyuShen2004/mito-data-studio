# Agent requirements

Read and preserve the product invariants in `docs/product-invariants.md`.
Changes to login, mock/development accounts, authentication, navigation, or
application reset must keep those behaviors and their regression tests unless
the user explicitly requests a change.

Development must exercise the same application production runs. The nine
`FEATURE_*` settings and the `VITE_*` build flags are aligned deliberately —
see "Feature flags" in `docs/development.md` before changing either side, and
change both together.

Any list endpoint that embeds `AnnotationTaskSerializer` must prefetch
`TASK_SELECT_RELATED` / `TASK_PREFETCH_RELATED` (`annotation/api.py`).
`review_history` walks submissions → reviews → reviewer per row, so without
them a 35-row page costs 415 queries instead of 53.
