# ADR-010 — Phase 12: chunk service and authorization

**Status:** accepted, 2026-07-30
**Phase:** 12 (chunk/datastore service)
**Depends on:** Phase 11 (ADR-009 locks the on-disk contract this serves)
**Gate:** **authz + metrics**
**Scope note:** [PHASE-12-scope-note.md](../phases/PHASE-12-scope-note.md)
**Amended by:** [ADR-009-A1](ADR-009-A1-region-mask-layer.md) — the chunk
address, capabilities and token claims carry a `layer` (2026-08-04)

---

## 1. The conflict worth resolving first

Doc 20's topology draws the **Chunk Svc as a separate process** from the Django
API, and its acceptance line is *"Django CPU not proportional to chunk QPS."*
Taken literally, Phase 12 would stand up a second deployable ASGI service.

**Resolution: build the chunk service as a separately-deployable *module*, not a
second running process — yet.** Three reasons, in order of weight:

1. **The gate is `authz + metrics`,** not "second process" and not a CPU
   benchmark. Row 13's gate is the p95 scrub target. Nothing in row 12 grades
   process topology.
2. **The acceptance line is about *scaling*, and the thing that actually makes
   Django CPU track chunk QPS is per-request ORM and serializer work.** A
   signed-token read that does **zero SQL queries** and returns raw bytes has
   removed that coupling; moving the same code into another process is then an
   ops decision, not an architectural one. This ADR requires the zero-query
   property and the benchmark asserts it.
3. **Standing up a second deployable would require the deployment work that is
   explicitly frozen** — new ports, new ingress, new systemd units, on a machine
   whose live service must not be touched. Doing that badly is precisely the
   failure mode the deployment-identity work spent a phase eliminating.

So: the read core is framework-independent (`chunks/core.py` imports no Django),
the HTTP surface is a thin Django mount, and a future ASGI host can import the
core unchanged. Recorded here so nobody later reads "we skipped the service".

## 2. Chunk identity — the canonical address

```
(volume_id, mag, cz, cy, cx)
```

| Element | Decision |
|---|---|
| `volume_id` | The `Volume` row. Permissions are always evaluated against it |
| `mag` | Array name from ADR-009 §3 — `"1"`, `"2"`, `"4"`. Validated against the derivative's own ladder, never against a client-supplied list |
| `cz, cy, cx` | **Chunk grid indices**, not voxel coordinates |
| Axis order | `(z, y, x)` — unchanged from ADR-009 |
| dtype | Whatever the derivative holds; never client-selectable |
| Channels | Single. Multi-channel is not in any Phase 12 source |

**Grid indices, not voxel boxes.** The chunk grid is the unit the store actually
has. An arbitrary voxel box would make the server read-modify-assemble across
chunk boundaries, which is both slower and *uncacheable* — two clients asking
for overlapping boxes share nothing. Indices make the cache key and the ETag
trivially correct. Voxel bounds are `index × chunk_shape` and are returned in
headers so a client never infers them.

**Edge chunks return their true clipped shape.** Padding to the nominal shape
would leave a client unable to tell data from filler at the boundary, and the
response already carries the shape.

## 3. Response contract

Raw little-endian bytes; metadata in headers. A client wanting pixels should not
parse a container to find them.

`Content-Type: application/octet-stream`, plus `X-Mito-Shape`, `X-Mito-Dtype`,
`X-Mito-Byte-Order`, `X-Mito-Mag`, `X-Mito-Chunk`, `X-Mito-Voxel-Offset`,
`ETag`, `Cache-Control: private, immutable, max-age=…`.

Chunks are immutable until a rebuild, so the ETag is a content hash and the
cache headers can be aggressive; a rebuild changes the build identity and
therefore the ETag.

**No filesystem path appears in any header, body or error, ever.** Errors are
structured JSON with a machine-readable `reason`.

## 4. Authorization — two paths on purpose

**Path A — authenticated application request.** Ordinary DRF authentication,
full permission check against the volume's project on every request. Correct,
and costs a database round trip. Used for capability inspection and low-rate
reads.

**Path B — signed chunk token.** Django issues a token after a full permission
check; the read path verifies the signature and the claims and touches **no
database**. This is what makes doc 20's acceptance line achievable.

Django remains the source of ACL truth: a token is a *cached authorization
decision with an expiry*, never an independent grant.

### Token claims

| Claim | Why |
|---|---|
| `v` schema version | So a format change is detectable, not silently misread |
| `k` key version | Rotation without invalidating everything at once |
| `d` deployment fingerprint | A token from another instance must not work here — the same wrong-instance failure the deployment work eliminated, arriving as a credential |
| `u` user id | Audit, and the ceiling below |
| `vol` volume id | Bound to one volume |
| `mags` allowed mags | A token for mag 4 must not read mag 1 |
| `scope` allowed chunk box | Optional; when present, bounds the readable region |
| `act` action | **`"r"` only.** There is no write token |
| `iat` / `exp` | Short life |
| `n` nonce | Distinguishes otherwise identical tokens in audit |

### What a token must never do

Contain a secret; widen its issuer's access; permit writes; live indefinitely;
expose a path; or work on another deployment.

### Revocation

**Short TTL (default 300 s) + key version + deployment binding.** Deliberately
*not* a revocation list: a per-request revocation lookup would reintroduce the
database read the token exists to avoid, defeating the point. Stated plainly so
the tradeoff is visible: **a token remains valid until it expires, so the TTL is
the revocation window.** Immediate global invalidation is available by rotating
the key version.

If a user's permission is removed after issuance, their outstanding token keeps
working until expiry. That is the documented policy, chosen over a per-read
check that would cost the property this design exists for.

### Signing

`django.core.signing` — HMAC-SHA256 over a key derived from `SECRET_KEY` with a
distinct salt, so a chunk token can never be confused with a session or password
reset token. Constant-time comparison is the library's. No new dependency, no
hand-rolled crypto.

## 5. Storage-handle caching

| Question | Decision |
|---|---|
| Key | `(volume_id, build_identity)` where build identity is the derivative's `built_at` |
| Invalidation | **Automatic** — a rebuild changes `built_at`, so the key changes and the old handle is simply never looked up again |
| Bound | LRU with a small max entry count; oldest evicted |
| Thread safety | A lock around the map; zarr handles themselves are read-only here |
| Fork | Handles are lazily created per process, so a forked worker builds its own rather than inheriting a half-initialised one |
| Failure | A read error evicts the entry and is reported as a typed error, not a stale handle |

Deriving invalidation from build identity rather than an explicit flush call is
the point: Phase 11 promotes a rebuilt derivative atomically, and a cache that
depended on someone remembering to flush would eventually serve a replaced store.

## 6. Job execution boundary

Five separable pieces, so the algorithm never depends on Slurm:

```
job specification   a plain dataclass — what to build, no scheduler concepts
job persistence     the existing ProcessingJob row
runner adapter      protocol: submit / poll / cancel
execution           local (in-process) or Slurm (sbatch/squeue/scancel)
result registration Phase 11's service validates and promotes
```

The Slurm adapter builds **argument arrays**, never shell strings, and validates
partition/resource names against configuration — the API never accepts a command.
States map to `queued / running / succeeded / failed / cancelled / unknown`, and
scheduler unavailability is `unknown` with a clear message rather than silent
failure. **Tests use a fake adapter; no real submission.**

## 7. Limits

Max chunk voxels and max response bytes, both configurable; mags restricted to
those the derivative actually has; dtype comes from the derivative only; chunk
indices are integers, bounds-checked against the array's grid — there is no
string path anywhere in the request, so path traversal has no surface.

## 8. Flag behaviour

`FEATURE_CHUNK_SERVICE` remains false in legacy profiles. As of 2026-08-04 it
defaults **True** in `production_integrated_v1`. Reading still requires
`FEATURE_VOLUME_PYRAMIDS`, and both retain explicit false rollback overrides.

| Configuration | Behaviour |
|---|---|
| Both off | Endpoints registered, return 503. Nothing else changes |
| Pyramids on, chunk service off | Derivatives build; **nothing is served** |
| Both on | Authenticated reads work; token issuance works |
| Flag on, no derivative | 404 with `reason: no_pyramid` |

**Endpoints never appear merely because pyramid files exist on disk.**

## 9. Metrics — the other half of the gate

Doc 23 names `chunk_fetch_seconds` and log fields `request_id, user_id,
chunk_key`. Phase 12 emits, in-process and readable through an authenticated
endpoint:

- `chunk_fetch_seconds` — histogram-ish summary (count, p50, p95, max)
- `chunk_bytes_total`
- `chunk_cache_hits_total` / `chunk_cache_misses_total`
- `chunk_rejected_total{reason}`
- `chunk_token_verify_seconds`

Row 19 owns Prometheus, Grafana, tracing and dashboards. Phase 12 owns *the
signals*, so that row 19 has something to scrape and this row's gate is
demonstrable now.

## 10. Migrations

**None expected.** Tokens are stateless; metrics are in-process; the runner
reuses `ProcessingJob`. The signing key derives from `SECRET_KEY`, so **no
signing secret is stored in the database**.

## 11. Test strategy

Framework-independent core tested directly; HTTP contract tested through the
views; tokens tested at their boundaries including clock skew, alteration,
wrong deployment and key rotation; limits tested with pathological input; runner
tested with a fake adapter across every state; metrics asserted to move.
