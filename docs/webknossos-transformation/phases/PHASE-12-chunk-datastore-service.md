# Phase 12 — chunk/datastore service

**Status:** **complete.** Authorization is pinned by tests for every way a token
can be wrong, and the metrics the gate names are recorded below.
**Depends on:** Phase 11 · **Gate:** **authz + metrics**
**Design:** [ADR-010](../adr/ADR-010-chunk-service-and-authorization.md) · [scope note](PHASE-12-scope-note.md)

---

## What shipped

| Area | Detail |
|---|---|
| Pure core | `volumes/chunks/core.py` — `ChunkAddress`, `HandleCache`, `read_chunk`. **Zero Django imports**, so the module stays separately deployable |
| Service | `volumes/chunks/service.py` — the two authorization paths, the flag gate, metric recording |
| Tokens | `volumes/chunks/tokens.py` — HMAC-SHA256 via `django.core.signing`, salt `mito.chunks.v1` |
| Metrics | `volumes/chunks/metrics.py` — bounded ring buffer; `chunk_fetch_seconds`, `chunk_token_verify_seconds`, cache hits/misses, rejections by reason |
| Endpoints | `volumes/chunks_api.py` — capabilities, authenticated read, token issue, signed read, metrics |
| Jobs | `volumes/pyramid/jobs.py` — submit / run / cancel / status over the existing `ProcessingBackend` registry |
| Flag | `FEATURE_CHUNK_SERVICE`, default **False**, and it requires `FEATURE_VOLUME_PYRAMIDS` as well |

## Completion gate — evidence

### Authorization

| Guarantee | Evidence |
|---|---|
| Authenticated permission checks work | `Permissions` — owner reads, manager reads |
| Cross-project access denied | `test_a_stranger_is_denied`, `test_a_stranger_may_not_issue_a_token` |
| Tokens are read-only | `test_a_token_is_read_only_by_construction` — the claim set has no write action to request |
| Token binds deployment, user, volume, mags, iat, exp, nonce, key version | `test_a_token_for_another_deployment_is_rejected`, `test_key_rotation_invalidates_outstanding_tokens`, `test_a_token_cannot_read_another_volume`, `test_a_token_cannot_read_a_mag_it_was_not_granted`, `test_a_scoped_token_cannot_read_outside_its_scope` |
| Malformed / expired / altered fail | `test_a_malformed_token_is_rejected`, `test_an_expired_token_is_rejected`, `test_a_token_from_the_future_is_rejected`, `test_an_altered_payload_fails_the_signature` |
| No per-request DB revocation query | benchmark asserts **≤1 query**, and that one is the primary-key volume fetch |
| Logs and responses carry no secret | `test_a_token_carries_no_secret`, `test_the_metrics_endpoint_exposes_no_identifiers_or_secrets`, `test_no_response_header_leaks_a_filesystem_path` |
| Failures are coarse, not a forgery oracle | `test_the_http_token_path_rejects_coarsely` |

### Chunk service

| Guarantee | Evidence |
|---|---|
| Address is `(volume_id, mag, cz, cy, cx)`, grid indices not voxel boxes | `ChunkAddress`; out-of-range and negative indices refused |
| Edge chunks return their true clipped shape | `test_an_edge_chunk_is_clipped_not_padded` |
| dtype and byte order preserved | `test_dtype_and_shape_are_exact`; little-endian normalised on the wire |
| **No full-volume load** | `StoreAccessShape` records the slice the store actually receives and fails if it exceeds one chunk |
| No user-controlled filesystem path | there is no path in the request at all; `core._unused_path_guard` raises if one is introduced |
| Source and derivative read-only | `test_reading_never_modifies_the_source_tiff`; no write path exists in `core` or `service` |
| Cache invalidated when build identity changes | `test_a_rebuild_invalidates_the_cache_without_an_explicit_flush` |
| Warm requests do not reopen the group | the group is opened **once** across ~90 reads (asserted on cache misses) |

### Jobs

| Guarantee | Evidence |
|---|---|
| Duplicate active builds rejected | `test_a_duplicate_active_build_is_refused` |
| Idempotent replay | `test_an_idempotency_key_replays_rather_than_duplicating` |
| Local runner tested | `Execution` — build, promote, `ready_streaming` |
| Slurm only via the registry, never submitted | `BackendRouting` — the adapter is resolved and its protocol checked; `external_job_id` stays empty |
| Failed builds do not replace a valid pyramid | `test_a_failing_run_records_the_failure_and_promotes_nothing` |
| Promotion invalidates stale handles | `test_a_rebuild_invalidates_the_chunk_handle_cache` |
| Temporary output cleaned on failure | the same test asserts `tmp_path` is gone |
| Status leaks no path | `test_status_reports_state_without_leaking_a_path` |

## Benchmark

8 × 2048 × 2048 uint16, mags 1/2/4, chunks `(1, 512, 512)`, local SSD:

| Metric | Value |
|---|---|
| Cold read (no cached handle) | 13.08 ms |
| Warm, same chunk | p50 **11.07** · p95 **11.83** · p99 11.83 ms |
| Sequential z scrub | p50 11.89 · p95 13.25 ms |
| Random z access | p50 12.04 · p95 14.31 ms |
| Token path | p50 **12.27** · p95 12.95 ms |
| Token verification | p50 **0.794 ms** |
| Cache hit ratio | **0.989** |
| SQL queries per token read | **1** (authenticated: 1) |
| Concurrent, 4 workers | 204 chunks/s · p50 18.73 · p95 21.42 ms · 1.37× serial |
| Concurrent, 8 workers | **223 chunks/s** · p50 31.14 · p95 **41.66** ms · 1.50× serial |
| Doc 23 SLO (chunk p95 < 150 ms warm) | **MET** |

The per-request ORM path lookup was removed: deriving the derivative's location
called `working_mask_stem`, whose collision rule queried sibling volumes on every
read. Serving from the path recorded in `pyramid_metadata` at promotion time
(`store.open_pyramid_at`) leaves exactly one primary-key fetch per token read,
which is what ADR-010 §1 rests on.

## Validation

Both gates green.

**Clean exclusive full backend suite** — `python manage.py test --noinput
annotation core volumes projects accounts processing`, nothing else touching the
database: **1203 tests, 1156.7 s, OK, exit 0, zero connection errors.**

**Fresh isolated checkout** — clean clone at the Phase 12 head, no uncommitted
files, its own venv (Python 3.11.15, declared dependencies only, no conda-global
packages), a disposable PostgreSQL database and a temporary `MITO_DATA_ROOT`:

| Step | Result |
|---|---|
| Dependency install | Django 5.1.15, zarr 3.1.6, psycopg 3.3.4 |
| System checks / deploy checks | no issues / 6 dev-config warnings, no errors |
| `makemigrations --check` / `migrate --plan` | no changes / plan clean |
| Migration from an empty database | 32 tables, exit 0 |
| Phase 11 pyramid tests | **58 OK** |
| Phase 12 chunk + token tests | **50 OK** |
| Pyramid job tests | **17 OK** |
| Chunk benchmarks | **3 OK** |
| Feature-flag smoke matrix (20 configs) | **99 OK** |
| Backend startup | serves on a disposable port |
| Authenticated chunk request | HTTP 200, 524 288 B = exactly one chunk |
| Signed-token chunk request | HTTP 200, byte-identical to the authenticated read; altered and missing tokens 403 |
| Frontend typecheck / tests / build | exit 0 · **78 passed** · built in 2.80 s |
| `git diff --check` | clean |

## Known limitations

> Activation update (2026-08-04): the chunk service and its pyramid dependency
> are enabled in `production_integrated_v1`; the production worker executes
> `build_pyramid` jobs and ready volumes are consumed by the real viewer/editor.

- **Revocation is bounded by the TTL, not immediate.** A token stays valid for
  its lifetime (default 300 s) even if the user's access is withdrawn. This is
  the deliberate trade in ADR-010 §4 — a per-request revocation check would put
  a database round trip back on the high-QPS path. Rotating
  `MITO_CHUNK_TOKEN_KEY_VERSION` invalidates every outstanding token at once,
  which is the emergency lever.
- **Teams are not a separate authorization axis here.** Chunk access follows
  project access, so with `FEATURE_TEAMS` off (the default and the tested
  configuration) a cross-team user is denied exactly because they are a
  cross-project stranger. If teams later widen project visibility, chunk access
  widens with it and will need its own test.
- **Metrics are in-process and unexported.** The ring buffer is per worker and
  resets on restart; there is no Prometheus endpoint. Doc 23's collection story
  is Phase 19.
- **Slurm is routing only.** Selecting the `slurm` backend records the choice and
  resolves the adapter; nothing is submitted and no scheduler was contacted.
  Making that path real is phase map row 18.
- **Cancellation covers queued jobs only.** The local backend runs the build
  inline, so a running build has no process to signal.
- **Concurrency is measured, not tuned.** 8 workers give 1.50× over serial —
  Python-level overhead, not I/O, is the ceiling. Adequate for the current SLO;
  revisit if the frontend scheduler in Phase 13 raises demand.
- **The handle cache is per process.** With 3 gunicorn workers a volume can be
  opened up to 3 times, and `invalidate_volume` only clears the worker that
  handled the rebuild. Other workers converge via build identity on their next
  read rather than instantly.
