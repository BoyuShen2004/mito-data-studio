# ADR-011 — Phase 13: frontend chunk cache and request scheduler

**Status:** accepted, 2026-07-31
**Phase:** 13 (frontend chunk cache/scheduler)
**Depends on:** Phase 12 (and therefore Phase 11)
**Gate:** **p95 slice step while scrubbing < 100 ms after warmup**

## 1. Scope resolution

The phase map calls row 13 “Frontend chunk cache/scheduler”; the master prompt
calls it a “Frontend PullQueue-like manager”. Row 14 is “Rendering/nav
redesign”. Documents for Phases 11 and 12 say that switching the editor read
path belongs to “Phase 13–14”, which is ambiguous in isolation.

The least destructive resolution is:

- Phase 13 implements the complete framework-independent PullQueue, strict
  Phase 12 client, decoded-byte cache, scrub planner, and a chunk-backed
  slice-data-source adapter.
- The adapter is available behind `VITE_FEATURE_CHUNK_PULL_QUEUE`, which is
  false unless explicitly set to `true`.
- Phase 13 validates the adapter in mounted tests and realistic scheduling
  benchmarks.
- It does **not** replace AnnotationCanvas or SliceViewer’s TIFF/PNG read path.
  Their rendering, labels, save, autosave and recovery behavior remain
  unchanged. Phase 14 owns mounting the adapter into the familiar viewer and
  the rendering/navigation decisions that entails.

This meets row 13 without silently doing row 14. A controlled transport
benchmark proves scheduler and decode latency; a same-LAN browser benchmark
must be repeated when Phase 14 mounts the adapter into rendering.

## 2. Request identity

A cache/request key is the canonical serialization of:

```
deployment fingerprint
volume id
pyramid build identity
magnification
chunk indices (z, y, x)
wire dtype/representation
authorization scope
```

No path or arbitrary URL is accepted. Deployment, build, volume and
authorization scope prevent reuse across releases, rebuilt derivatives,
projects or users. The Phase 12 capabilities response is extended additively
with `build_identity`; chunk responses echo it as `X-Mito-Build-Identity`.
This is necessary because ETags alone are learned after a request and cannot
make a pre-request memory-cache key safe.

## 3. PullQueue

Priority classes, highest first:

1. `CURRENT` — current visible slice/chunks;
2. `REFINE` — higher-resolution replacement for a visible fallback;
3. `NEAR` — predicted next scrub slices;
4. `PREFETCH` — speculative work.

Within a class, insertion order is stable. Reprioritization is explicit.
Defaults are six active requests globally and four per volume, both
configurable and capped by a bounded pending queue. A runnable request from
another volume is not blocked behind a saturated volume.

An adapter owns its queue by default. Phase 14 may inject one app-scoped queue
to make the global cap span multiple viewers; the injecting caller owns its
lifecycle. Disposing one adapter then cancels only that adapter's consumers and
cannot terminate another viewer's shared request.

Identical keys collapse to one underlying request and share its result.
Consumers have independent cancellation; the underlying `AbortController` is
aborted only when no consumer remains. Cancelling a viewport generation drops
queued work and aborts unshared in-flight work. A newer generation is recorded
per viewport. A completion from an older generation rejects as stale and is
never handed to display code.

Retry is configurable. Only typed transient/network errors retry, with bounded
exponential backoff and optional jitter; aborts, authorization errors, malformed
responses and missing chunks do not. Disposal aborts everything, rejects
pending consumers and releases references. Metrics hooks report enqueue/start,
dedupe, cancel, stale, retry and completion.

## 4. Scrubbing

The latest target always wins:

- the target slice is `CURRENT`;
- the previous generation is cancelled;
- neighbors in the current direction are `NEAR`, followed by the opposite
  neighbor;
- reversal immediately changes prediction and cancels obsolete speculative
  work;
- boundaries are clipped and repeated targets deduplicate;
- large jumps discard the old window;
- during rapid motion the planner selects a coarser available magnification
  (prefer mag 2, then 4) and schedules mag 1 as `REFINE` only after settle;
- no lower-resolution data is silently presented as full-resolution: result
  metadata carries its magnification.

The adapter assembles XY planes because ADR-009’s chunks are slice-oriented.
XZ/YZ rendering and progressive visual composition remain Phase 14.

## 5. Cache

Four concerns remain separate:

- in-flight collapse belongs to PullQueue;
- decoded chunks live in a byte-bounded LRU (64 MiB default);
- browser HTTP storage is bypassed for chunk reads in Phase 13;
- no persistent cache is introduced.

Phase 12's URL does not contain build identity and its signed URL also does not
contain volume identity. Consequently `private, immutable` is not a sufficient
browser cache key: after rebuild it can return an old build, and two signed
volume reads can share the same URL. Chunk fetches therefore use
`cache: "no-store"` and all reuse goes through the complete Phase 13 identity.
A future versioned URL may safely restore the HTTP-cache layer.

The LRU counts `ArrayBuffer.byteLength`, not entries. Reads refresh recency;
eviction is oldest-first and deterministic. Identity includes deployment and
build, so a rebuild or deployment change cannot hit an old entry. Volume
switches use distinct keys; scoped clearing and disposal release references.
Large decoded arrays never enter React state.

## 6. Signed tokens

Tokens are requested on first signed read and held only in memory. The provider
caches them until a configurable expiry skew, collapses concurrent refreshes,
and scopes them by deployment, volume, magnifications and authorization scope.
Changing any scope disposes the provider. A signed read sends the credential in
`X-Mito-Chunk-Token`, never logs it, and never persists it or places it in a
query string.

One 401/403 invalidates and refreshes once. A second authorization failure is
returned. Refresh, retry and disposal are abortable. Permission/deployment
changes require a new provider and therefore a new token.

## 7. Response validation and limits

The client requires `application/octet-stream`, little byte order, exact
requested mag/index headers, exact build identity, three positive integer shape
and offset fields, a supported dtype, and an exact byte count. Edge chunks may
be smaller than the nominal chunk shape but may not exceed it or the advertised
array bounds. Content-Length and the received buffer are bounded (32 MiB
default). Malformed responses fail with typed errors and never populate cache.

## 8. Feature and backend boundaries

`VITE_FEATURE_CHUNK_PULL_QUEUE=false` is the default. This phase adds no model,
migration, write endpoint or persistent state. The only backend change is the
backward-compatible build-identity metadata needed for correct invalidation.
Phase 12 authorization and URL contracts otherwise remain unchanged.

## 9. Performance and evidence

The initial target remains doc 21:

- warm scrub p95 < 100 ms;
- no more than 12 in flight (default 6);
- browser heap flat within +15% after warmup;
- TTFV < 1 s warm / < 3 s cold on the same LAN.

Unit tests cover ordering, fairness, concurrency, dedupe, cancellation, stale
generations, retries and disposal. Client tests cover token refresh, strict
headers, edge chunks and size limits. Adapter/mounted tests cover scrub
patterns, build/volume isolation and the disabled flag. The benchmark exercises
cold/warm sequential and random scrubs for 512² and 2048² planes at mags 1/2/4;
it records queue wait, transport, decode, cache, cancellations, stale results,
dedupe and memory.
