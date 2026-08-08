# ADR-001 — Control plane / data plane split, and criteria for a language migration

**Status:** Accepted
**Date:** 2026-07-27
**Decision maker:** repository owner
**Supersedes:** nothing
**Related:** `benchmarks/BASELINE.md`, research docs `20`, `21`, `09`

---

## Context

WEBKNOSSOS achieves its responsiveness with a Scala/JVM stack split across
`app`, `webknossos-datastore`, and `webknossos-tracingstore`. It is tempting to
read "match WEBKNOSSOS quality" as "adopt WEBKNOSSOS's languages".

That inference is unsupported. The Phase 0 baseline shows *where* mito is slow
and *why*, and the causes are architectural, not linguistic:

| Measurement | Value | Cause |
|---|---|---|
| Scrub p95, liver (17.7 MPix/slice) | **659 ms** vs 100 ms target | Full-resolution JPEG encode of a whole plane, ~34 ms/MPix |
| Throughput at 1 / 4 / 12 threads | **1.3–1.7 slice/s, flat** | Fully serialized encode (GIL-bound) |
| p95 at 12 concurrent | **10.9 s** | Same |
| Paint commit, liver | **285 ms/stroke** | Whole-slice RLE PUT; cost tracks the plane, not the stroke |
| Concurrent task claims | **87–95 % fail** | SQLite global write lock; `select_for_update()` is a no-op on SQLite |

None of these are "Python is slow". Encoding 17.7 megapixels costs ~600 ms in
any language if you insist on encoding 17.7 megapixels; **at mag 4 the same
plane is 1.1 MPix ≈ 38 ms**, comfortably inside target. The concurrency
collapse is a serialized-resource problem, and the claim failures are a
database-engine problem. A rewrite would fix none of them; multiresolution
data, a separate data path, and Postgres fix all of them.

## Decision

**1. Keep React + Django REST Framework + PostgreSQL.** No backend-language
migration now. Backend-language choice is a **separate, evidence-based
decision**, deferred until the measures in this ADR are collected.

**2. Split the system conceptually into two planes**, so each can be scaled,
profiled, and (if ever necessary) reimplemented independently.

### A. Control plane — stays Django

Users, teams, projects, tasks, assignments, reviews, permissions, audit logs,
progress statistics.

Request rates are human-scale (clicks, form posts). Correctness, transactional
integrity, and permissions dominate; throughput does not. Django + DRF +
PostgreSQL is well suited and the domain logic already lives there. **There is
no performance argument for moving the control plane, and none is expected.**

### B. Data plane — Django for now, extractable later

Volume metadata, chunk retrieval, multiresolution data, compression, caching,
request prioritization, streaming, signed/authorized data access.

This is where the baseline hurts. It is **byte-shuffling under concurrency** —
the workload where runtime choice can matter. But the dominant costs today are
algorithmic (no mags, whole-plane encode, whole-slice writes), so those are
fixed first, in place, and re-measured.

**3. Extraction before rewriting.** If the data plane must leave the Django
process, the first step is a **separate Python service** (async, TensorStore,
its own process pool) — not a different language. This alone removes the GIL
coupling, unblocks app workers, and is a fraction of the cost of a rewrite.

**4. A language migration requires benchmark evidence.** The bar is in §
"Migration criteria" below. Recommending Scala, Java, Go, or Rust **without
that evidence is out of scope**, and proposing a full backend rewrite is an
explicit stop-and-ask condition.

## Sequencing

| Step | Action | Expected effect |
|---|---|---|
| 1 | Multiresolution pyramids (Phase 11) | Attacks the 34 ms/MPix constant directly; predicted p95 ≈ 38 ms at mag 4 |
| 2 | Op-log / chunk-delta writes (Phase 7) | Decouples paint cost from plane size |
| 3 | Postgres (Phase 1–3 foundation) | Fixes the 87–95 % claim failure rate |
| 4 | **Re-measure** | Most targets are expected to be met here |
| 5 | Extract the Python chunk service (Phase 12) — only if step 4 falls short | Removes GIL coupling, isolates app workers |
| 6 | **Re-measure** | |
| 7 | Consider a non-Python data plane — only if step 6 falls short against the criteria below | |

Each numbered step must produce a benchmark artifact before the next begins.
Skipping to step 7 is not permitted.

---

## Migration criteria

A non-Python data plane may be **proposed** only when a benchmark report shows
the optimized Python data plane (steps 1–6 complete) fails the targets below,
**and** profiling attributes the failure to runtime characteristics — GIL
serialization, interpreter overhead, GC pauses, memory model — rather than to
algorithm or I/O.

Measure on the Phase 0 corpus (`/home/weidf/shenb/wk_data`), largest volume
(liver, 160 × 3885 × 4544), warm cache unless stated.

### Threshold table

| # | Metric | Target | Baseline (2026-07-27) | Fails if |
|---|---|---|---|---|
| 1 | **Sustained concurrent chunk requests** | ≥ 200 req/s at ≤ 12 in-flight | 1.3 slice/s | < 100 req/s |
| 2 | **Chunk latency p50** | < 50 ms | 596 ms (full plane) | > 100 ms |
| 3 | **Chunk latency p95** | < 150 ms | 659 ms | > 300 ms |
| 4 | **Chunk latency p99** | < 400 ms | not measured | > 800 ms |
| 5 | **Throughput ceiling** | scales ≥ 4× from 1 → 12 workers | **1.0× (flat)** | < 2× |
| 6 | **CPU efficiency** | ≥ 60 % of cores usable for chunk serving | 1 core effective | < 30 % |
| 7 | **Memory** | steady-state RSS < 2 GB/worker; growth < 15 %/2 h | +1.2 GB per volume, no eviction | unbounded growth |
| 8 | **Worker saturation** | app workers < 20 % busy on chunk I/O | ~100 % | > 50 % |
| 9 | **Timeout / error rate** | < 0.1 % at target concurrency | not measured | > 1 % |
| 10 | **Multi-annotator** | 10 concurrent annotators, no p95 regression > 2× single-user | untested (fails at 4) | > 4× |
| 11 | **Slow storage** | graceful degradation at 100 MB/s, no collapse | untested | queue collapse / unbounded latency |
| 12 | **Slow network** | usable at 50 Mbit / 50 ms RTT via mag fallback | untested | unusable |

### Evidence required with any migration proposal

1. Benchmark report covering **all twelve** rows, before and after steps 1–6.
2. **Profile** (`py-spy` / `cProfile` / `perf`) attributing the shortfall to
   runtime characteristics, with the specific mechanism named.
3. Evidence that the algorithmic fixes were actually applied — a proposal
   citing pre-pyramid numbers is invalid.
4. A prototype of the hot path in the candidate language, benchmarked on the
   **same corpus**, showing it clears the thresholds.
5. Migration cost estimate: scope, risk, rollback, and who maintains a
   polyglot deployment.
6. Explicit statement of what is **not** migrating (the control plane stays
   Django).

### Anti-criteria — never sufficient on their own

- "WEBKNOSSOS uses Scala."
- Microbenchmarks not on this corpus.
- Language preference, or a claim that Python "cannot" do X without a profile.
- Benchmarks taken before pyramids and op-log writes land.

---

## Consequences

**Accepted:**
- Some effort may later prove to have been spent optimizing a component that is
  eventually replaced. Acceptable: the algorithmic fixes (mags, deltas) are
  prerequisites for *any* implementation and are not wasted.
- Polyglot deployment stays off the table for now, keeping ops simple.

**Risks:**
- If the data plane does eventually need a different runtime, extraction work
  (step 5) partially carries over — the service boundary and authz model
  survive; the implementation does not.
- Team familiarity with a candidate runtime is not assessed here and must be
  part of any proposal.

**Revisit when:** step 4 or step 6 re-measurement completes, or any threshold
above is breached in production.
