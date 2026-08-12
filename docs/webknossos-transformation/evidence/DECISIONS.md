# Project decisions log

Durable decisions made with the user during the WEBKNOSSOS transformation.
Each entry records what was decided, when, and what it changes downstream.

---

## D1 — mito-data-studio will **not** incorporate AGPL WEBKNOSSOS code

**Date:** 2026-07-27
**Decided by:** user, at the post-Phase-0 architecture gate
**Status:** ⚠️ **SUPERSEDED by [D3](#d3--agpl-reuse-is-permitted-with-provenance-obligations) later the same day.**
Kept for the record. The revised reuse matrix lives in D3; the one below is
no longer in force.

### Decision

Keep AGPL out of mito-data-studio. WEBKNOSSOS behaviour is reproduced by
**independent reimplementation** from documentation and published methods.
Permissive components (MIT / Apache / BSD) may be used directly.

### What this overrides

The master prompt (§A, §H) and doc `02` both permit AGPL-compliant reuse, and
doc `L`'s reuse matrix assumed line-level porting was available. **That matrix
is superseded by the table below.** Doc `02`'s "user instruction overrides
aversion" note no longer applies — the user has now given the opposite
instruction, and it governs.

### Revised reuse matrix

| Area | Pack default (`L`) | **Now** |
|---|---|---|
| Task hierarchy | Reimplement in Django | unchanged — reimplement |
| Claim locking | Reimplement WK strategy | unchanged — reimplement (behaviour, from `05`/`AUDIT_DELTA`) |
| Auto-fill scheduler | New | unchanged — new |
| Review / QC / hard-case | Retain mito | unchanged |
| **Interpolation** | "Port algorithm (SDF)" | **Independent reimplementation.** SDF + linear blend is a standard published method; implement from the description in `07`, not from `volume_interpolation_saga.ts`. Do not copy or transliterate that file. |
| **Chunk pull scheduling** | "Port concepts from PullQueue" | **Concepts only** — priority queue, batching, abort-on-stale are generic. Do not port `pullqueue.ts` structure or code. |
| **Datastore** | New Python service; "optional WK AGPL datastore later" | **New Python service only.** The AGPL datastore option is withdrawn. |
| **Zarr tooling** | "TensorStore preferred; `webknossos` package if AGPL accepted" | **TensorStore / zarr-python only.** The `webknossos` PyPI package is AGPL — **do not add it as a dependency.** |
| `cluster_tools` | MIT reuse OK | unchanged — MIT, reuse with attribution |
| Proofreading UI / SAM | Retain mito | unchanged |

### Engineering rules that follow

1. **No copying, transliterating, or line-level adaptation** of any file from
   `scalableminds/webknossos` or the `webknossos` Python package.
2. The clone at `/home/weidf/shenb/external-research/webknossos` is for
   **reading and behavioural verification only**. It stays outside the mito
   repo and is never vendored.
3. `webknossos` (PyPI) must not enter `environment.yml` or any lockfile.
   `cluster_tools` (MIT) is fine.
4. Citing WK behaviour in comments and docs is encouraged and is **not** a
   licence event — describing what a system does carries no obligation.
5. Every phase still records its inspiration in the attribution register, now
   with relationship `independently reimplemented` rather than `ported`.

### Consequences to accept

- Phase 8 (interpolation) must reach WK parity from the algorithm description
  plus the golden-mask tests in `19`, without consulting the saga line by line.
  The description in `07` was verified accurate during the audit, so this is
  workable.
- Phase 12 (datastore) has no fallback to a proven implementation.
- mito-data-studio still needs its own LICENSE file chosen before distribution
  (`26` checklist item 1) — **this decision does not by itself pick one.** It
  only establishes that the licence need not be AGPL-compatible.

---

## D2 — Repair the in-flight `label_type` regressions before Phase 1

**Date:** 2026-07-27
**Decided by:** user, at the post-Phase-0 architecture gate
**Status:** done (Phase 0.5)

The user's uncommitted WIP tightened volume-registration `label_type`
semantics and left 9 tests red (HEAD was green at 248/248). The user asked for
these to be fixed rather than fixing them themselves.

**Outcome:** suite green at **293 tests**. See `benchmarks/BASELINE.md` §2.

Changed files (4): `volumes/services.py`, `volumes/api.py` (code);
`volumes/tests.py`, `annotation/test_api_flows.py` (tests). The user's WIP in
all other files was left untouched.

**Extended on user instruction** to sweep every code path coupling
`label_path` / `label_type` / mask / `prediction|partial|proofread|none`, not
just the original nine failures. Four confirmed defects fixed; see
`benchmarks/BASELINE.md` §2.1. The user's constraint governed the edit-path fix:
attaching a mask must **not** silently become `prediction` — so that case raises
an actionable error instead of guessing.

---

## D3 — AGPL reuse is permitted, with provenance obligations

**Date:** 2026-07-27
**Decided by:** user, at the architecture-approval gate
**Status:** active — **supersedes [D1](#d1--mito-data-studio-will-not-incorporate-agpl-webknossos-code)**

### Decision

The user accepts AGPL obligations for the prototype where WEBKNOSSOS code is
directly reused. **AGPL status alone is not a reason to block or degrade an
implementation.**

Reuse is nevertheless the *exception*, not the default:

> Prefer WEBKNOSSOS as an **architecture and behavioural reference**. Direct
> source reuse is allowed **only when it provides a substantial engineering
> benefit and its provenance is documented.**

### Standing obligations (all mandatory, no exceptions)

1. **Never remove or alter copyright notices** on reused code.
2. **Never mislabel** copied or derived AGPL code as MIT or any other licence.
3. **Record every reused file or component** and its original licence in
   `attribution/REGISTER.md`.
4. Maintain **`THIRD_PARTY_NOTICES.md`** at the repo root.
5. **Keep original and reused code physically separate** — reused WEBKNOSSOS
   code lives under a clearly marked path, never interleaved into mito modules.
6. **Flag repository-wide relicensing consequences *before* committing** any
   copied source. This is an explicit stop condition.

### Reuse test — apply before copying anything

Copy only when **all** hold; otherwise reimplement from the behavioural spec:

| # | Question |
|---|---|
| 1 | Does copying save substantial engineering effort *or* materially reduce correctness risk? |
| 2 | Is the mito equivalent likely to be worse if written fresh (subtle edge cases, hard-won fixes)? |
| 3 | Can provenance be recorded precisely (upstream path + commit)? |
| 4 | Can it live in an isolated, clearly marked module? |
| 5 | Have the relicensing consequences been stated to the user? |

Test 5 failing is a **stop**, not a judgement call.

### Reuse matrix (replaces both `L` and D1)

| Area | Decision | Rationale |
|---|---|---|
| Task hierarchy | Reimplement in Django | Scala/Slick, no portable surface |
| Claim locking | Reimplement behaviour | Semantics port; SQL does not |
| Auto-fill scheduler | New | Beyond WK |
| Review / QC / hard-case | Retain mito | mito is ahead here |
| **Interpolation (Phase 8)** | **Reimplement first; copying permitted if parity stalls** | Algorithm is published + `07` verified accurate line-for-line in the audit. Revisit against the reuse test if golden-mask parity proves hard. |
| **PullQueue concepts (Phase 13)** | Reimplement | Priority/batch/abort are generic; a TS→TS copy would drag in WK's bucket model |
| **Datastore (Phase 12)** | New Python service; **AGPL WK datastore now a live fallback** | D1 had withdrawn this option; it is restored |
| **`webknossos` PyPI package** | **Permitted** (AGPL) where it beats hand-rolled Zarr IO | D1's ban is lifted. Still prefer TensorStore/zarr-python where equivalent. |
| `cluster_tools` | Reuse (MIT) | Permissive |
| Proofreading UI / SAM | Retain mito | mito is ahead |

### Repository-wide consequence (not yet triggered)

mito-data-studio has **no LICENSE file** (audit §1). Committing AGPL-derived
source into the served product would oblige the combined work to be offered
under AGPL-compatible terms with a §13 source offer for network users. **No
AGPL code has been copied as of this entry**, so nothing is triggered yet. The
moment a phase proposes to copy, that phase stops for user sign-off first.
