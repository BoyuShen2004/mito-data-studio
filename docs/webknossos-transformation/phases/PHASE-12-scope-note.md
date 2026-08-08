# Phase 12 — scope note

**Official title (phase map row 12):** **Chunk/datastore service**
**Depends on:** Phase 11
**Gate (phase map):** **authz + metrics**
**Design record:** [ADR-010](../adr/ADR-010-chunk-service-and-authorization.md)

---

## 1. Authoritative sources

| Source | What it fixes |
|---|---|
| `27-claude-code-phase-map.md` row 12 | `Chunk/datastore service \| depends 11 \| gate: authz + metrics` |
| `CLAUDE_CODE_MASTER_PROMPT.md` §E11–E13 | *"Chunk service with token auth (Phase 12)."* |
| `20-target-volume-infrastructure.md` §Target topology | Django API owns *"authz, tasks, metadata, chunk tokens"*; Chunk Svc owns *"async read, cache, compress, metrics"* |
| `20` §Authz | *"Chunk service validates signed token: `user, volume_id, layers[], exp, read\|write`. Django remains source of ACL truth."* |
| `20` §Acceptance | *"Django CPU not proportional to chunk QPS."* |
| `23-target-observability-design.md` | `chunk_fetch_seconds` is the named chunk metric; log fields `request_id, user_id, chunk_key`; **SLO: chunk p95 < 150 ms local SSD warm** |
| `14` gap matrix row 30 | Chunk streaming: WK PullQueue vs mito per-slice JPEG → **Critical**, "Redesign" |
| ADR-009 + Phase 11 docs | The on-disk contract this serves; `read_plane` opens the group per call, flagged for this phase |

**The gate is two words and both are checkable.** *authz* = signed tokens that
bind and are enforced. *metrics* = `chunk_fetch_seconds` and friends actually
emitted and readable, not a dashboard (row 19 owns dashboards).

## 2. Required functionality

1. A **bounded chunk read service** over the Phase 11 Zarr v3 pyramid, with
   **cached** group/array handles — Phase 11 explicitly deferred this.
2. **Signed chunk tokens**: issued by Django, verified on read, binding at
   minimum user, volume, mags, scope, action, deployment, issued-at and expiry.
3. **Authenticated HTTP endpoints** for capability inspection, chunk read and
   token issuance.
4. **Metrics** on the chunk path: `chunk_fetch_seconds`, cache hit ratio,
   bytes served, rejections by reason.
5. **Django remains the source of ACL truth** — a token can never widen what its
   issuer could do.

## 3. Explicit exclusions

| Excluded | Owner |
|---|---|
| Frontend PullQueue / chunk cache / scheduler | **Phase 13** (gate `p95 scrub target`) |
| Switching the editor from TIFF to pyramid reads | Phases 13–14 |
| Editor scrub prefetch | Phase 13 |
| Prometheus/Grafana stack, dashboards, tracing, Sentry | **Phase 19** (`Observability`, gate `dashboards live`) |
| Mesh APIs | Phase 15 |
| Annotation delta chunk store | Doc 20 §Format plan item 3; not row 12 |
| A CDN or Cloudflare integration | Not in any Phase 12 source |

## 4. Dependencies

Phase 11 only, plus the existing `volumes`/`projects` permission surface and
`core.data_root`. Must work with every annotation flag off.

## 5. Chunk request model

A read is addressed by **volume + mag + chunk grid index**, never by a path:

```
volume_id : int          the Volume row; permissions are checked against it
mag       : str          array name from ADR-009 — "1", "2", "4", …
cz, cy, cx: int          chunk *grid* indices, not voxel coordinates
```

Chunk indices rather than voxel bounds, because the chunk grid is the unit the
store actually has: an arbitrary voxel box would force read-modify-assemble on
the server and make caching meaningless. Voxel bounds are derivable
(`index × chunk_shape`) and are returned in the response metadata so a client
never has to guess.

Axis order `(z, y, x)`, dtype preserved from the derivative, single channel —
all inherited unchanged from ADR-009 §3.

**Edge chunks are returned at their true (clipped) shape**, not padded to the
nominal chunk shape. Padding would make a client unable to distinguish real data
from filler at the volume boundary, and the response carries the shape so there
is nothing to infer.

## 6. Response representation

Raw little-endian array bytes, with metadata in headers rather than a wrapper —
a client that wants pixels should not have to parse a container to find them.

| Header | Meaning |
|---|---|
| `Content-Type` | `application/octet-stream` |
| `X-Mito-Shape` | `z,y,x` of the returned block (clipped at edges) |
| `X-Mito-Dtype` | e.g. `uint16` |
| `X-Mito-Byte-Order` | `little` |
| `X-Mito-Mag` | echoed mag |
| `X-Mito-Chunk` | echoed `cz,cy,cx` |
| `X-Mito-Voxel-Offset` | voxel origin of the block |
| `ETag` | content hash — chunks are immutable until a rebuild |
| `Cache-Control` | private, immutable, bounded max-age |

**No filesystem paths are ever returned**, in any header, body or error.

Errors are structured JSON with a machine-readable `reason`, matching the Phase
10 convention.

## 7. Authorization

Two paths, deliberately distinct:

1. **Session/token-authenticated application requests** — ordinary DRF auth,
   full permission check against the volume's project on every request.
2. **Short-lived signed chunk tokens** — for the high-QPS path, so a chunk read
   need not hit the database. This is what makes *"Django CPU not proportional
   to chunk QPS"* achievable.

A signed token binds: schema version, key version, deployment fingerprint, user
id, volume id, allowed mags, allowed chunk scope, action (**read only**),
issued-at, expiry, nonce.

It must never: contain secrets, widen the issuer's access, permit writes, be
valid indefinitely, expose paths, or be valid on another deployment.

**Revocation** is by short TTL (default 5 minutes) plus key version plus
deployment binding — not a revocation list, which would reintroduce the
per-request database read the token exists to avoid. Documented plainly: a token
remains valid until it expires, so the TTL *is* the revocation window.

## 8. Storage-handle caching

Keyed by `(volume_id, pyramid build identity)`. Bounded LRU, evicting oldest.
The build identity comes from the derivative's `built_at`, so a **rebuild
invalidates the cache automatically** rather than relying on anyone remembering
to flush it. Thread-safe; never keeps a handle to a replaced store.

## 9. Job runner

Doc 20 names `ProcessingJob(type=build_pyramid) → Slurm/local`. Phase 11 added
the job type and the service; Phase 12 adds the **runner boundary**: a runner
protocol, a local runner, and a Slurm adapter that builds argument arrays rather
than shell strings. The build algorithm must not depend on Slurm.

**No real Slurm submission in tests** — a fake adapter and dry-run validation
only.

## 10. HTTP endpoints

```
GET  /api/volumes/<pk>/chunks/capabilities/         mags, shapes, chunk shape, dtype
GET  /api/volumes/<pk>/chunks/<mag>/<cz>/<cy>/<cx>/ authenticated chunk read
POST /api/volumes/<pk>/chunks/token/                issue a signed token
GET  /api/chunks/signed/<token>/<mag>/<cz>/<cy>/<cx>/  token chunk read
GET  /api/chunks/metrics/                           chunk-service metrics
```

Registered unconditionally, returning 503 when the flag is off — the Phase 3/6
convention. **Endpoints must not appear merely because pyramid files exist.**

## 11. Migrations

Expected **none**. Tokens are stateless and signed; metrics are in-process; the
runner reuses the existing `ProcessingJob`. A signing key lives in settings, not
the database.

## 12. Feature flag

**`FEATURE_CHUNK_SERVICE`**, default **False**. Reading also requires
`FEATURE_VOLUME_PYRAMIDS`, since without a derivative there is nothing to serve.

## 13. Performance targets

| Metric | Target |
|---|---|
| Chunk p95, warm cache | **< 150 ms** (doc 23 SLO) |
| Warm vs cold | warm materially cheaper — the cache must earn its place |
| SQL queries per token-authenticated chunk read | **0** |

## 14. Security limits

Max chunk dimensions and response bytes; mag allow-list from the derivative;
dtype from the derivative only; no user-controlled paths; chunk indices are
integers and bounds-checked (no traversal surface); token issuance is
permission-gated; malformed tokens rejected without leaking why.

## 15. Tests and completion gate

1. Chunk reads: valid, edge, out-of-range, invalid mag, missing pyramid, dtype
   and shape exact, deterministic bytes, rebuild invalidation, source unchanged.
2. Authorization: unauthenticated, cross-project, cross-team, manager,
   annotator, another project's volume.
3. Tokens: valid, expired, malformed, altered payload, wrong deployment, wrong
   volume, wrong mag, wrong scope, write attempt, key rotation, reuse within TTL.
4. Limits: oversized request, pathological coordinates, unsupported dtype.
5. Jobs: local success/failure, duplicate, cancel, fake Slurm states, scheduler
   unavailable.
6. **Metrics emitted and readable** — the second half of the gate.
7. Smoke matrix extended; benchmarks recorded cold vs warm.

## 16. Relationship to Phase 13

Phase 13 builds the frontend PullQueue against these endpoints and is graded on
`p95 scrub target`. Phase 12 must therefore leave a **stable HTTP contract** and
must not implement any client-side scheduling, caching or prefetch.
