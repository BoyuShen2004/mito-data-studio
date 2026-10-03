# Third-Party Notices

Third-party software distributed with, vendored into, or reused by
mito-data-studio, with its original licence.

This file records components currently included or used by the project.
Add an entry before committing copied, ported, or vendored code. Dated audit
results below describe those checks, not a fresh audit of every dependency.
The concise companion register is
[`docs/attribution.md`](docs/attribution.md).

The project remains under license review; see [LICENSE](LICENSE) for its
current distribution policy. Third-party components retain their own licenses
and rights. This file does not grant an exception to those terms.

---

## 1. Vendored components (present in this repository)

| Component | Path | Upstream | Licence | Relationship |
|---|---|---|---|---|
| SAM 2 | `vendor/sam2/` | facebookresearch/sam2 (`2b90b9f5ceec907a1c18123530e92e794ad901a4`) | Apache-2.0 (`vendor/sam2/LICENSE`) | 22 source/config files matching the pinned tree + official SAM 2.1 checkpoint |
| em_erl skeleton helpers | `backend/annotation/third_party/em_erl_skel.py` | PytorchConnectomics/em_erl (`b1504f2c3edbece34efc417c395432692d54e14d`) | MIT (`backend/annotation/third_party/LICENSE.em_erl`) | `em_erl/skel.py` from the pinned tree with one documented change (a `progress` keyword) and a provenance header |

### Recorded verification

SAM 2 was checked on 2026-09-09; the em_erl comparison was recorded on
2026-10-01. The following records are retained as provenance evidence:

| Question | `vendor/sam2` | `backend/annotation/third_party/em_erl_skel.py` |
|---|---|---|
| `LICENSE`/`COPYING` present? | **Yes** — Apache-2.0 | **Yes** — MIT (`LICENSE.em_erl`) |
| Upstream commit pinned? | **Yes** — commit above | **Yes** — commit above |
| Tracked in git? | Yes — 25 files | Yes — 3 files (module, licence, `__init__.py`) |
| Source code or weights only? | **Source** (21 `.py`) + `.pt` checkpoint | **Source** (1 `.py`) |
| Copyright headers present? | **Yes** — `Copyright (c) Meta Platforms, Inc. and affiliates.` | Upstream file has none; the vendored copy adds a provenance header carrying `Copyright (c) 2024 Pytorch Connectomics` from the upstream LICENSE |

The SAM2 source/config files matched every corresponding file at the pinned
official commit, and the checkpoint downloaded from the official URL matched
`2647878d…`. The upstream Apache-2.0 text is carried in-tree beside it.

Checked 2026-10-01: the em_erl file matches `em_erl/skel.py` at the pinned
commit except for the provenance header and one documented change (a
`progress` keyword passed through to kimimaro), verified with `diff` against
the upstream file. The upstream MIT text is carried beside it as
`LICENSE.em_erl`.

---

## 2. Python and JavaScript dependencies

Ordinary dependencies declared in `requirements/release.txt` and
`frontend/package-lock.json` retain their own licences. Run
`ops/release/audit_dependency_licenses.py` from the release environment after
`npm ci` to reproduce the machine-readable inventory. The historical v1.1.0 audit covered
55 Python distributions and 181 JavaScript packages and found no package with
missing licence metadata or licence file. Regenerate the inventory for a new
release; those counts do not describe the current dependency set.

The inventory is not uniformly permissive. In particular it records psycopg
(LGPL-3.0-only), tqdm (MPL-2.0 and MIT), caniuse-lite (CC-BY-4.0), NVIDIA CUDA
runtime wheels (NVIDIA proprietary terms), and the notices embedded in the
SciPy binary wheel (including GCC Runtime Library Exception and libquadmath).
Those original notices must remain available with any redistributed runtime.

### Measurement dependencies — pending license review

Recorded 2026-10-01: `kimimaro`
(GPL-3.0-or-later) for TEASAR skeletonization in
`backend/annotation/measurements.py`, with its dependencies `dijkstra3d`
(GPL-3.0-or-later) and `connected-components-3d`, `edt`, `fill-voids`,
`xs3d` (LGPL-3.0-or-later) and `fastremap` (LGPL-3.0). Its other new
transitive dependencies are BSD or MIT. kimimaro is imported lazily, only
when a measurement runs; lazy importing does not resolve the licensing
requirements of distributing the integrated software. Licenses were read from
installed package metadata; this record is not a legal compliance finding.

## 3. Ported code with unresolved provenance review

Several modules in `backend/annotation/cellable_port/` were ported from the
Cellable desktop annotator. Their source notes identify the originating
functions, but the upstream license and redistribution terms remain to be
verified. See [the attribution register](docs/attribution.md#cellable-provenance).

Before an external release, resolve these terms and the measurement dependency
review, preserve applicable copyright/license notices, and regenerate the
release dependency inventory. Recording a component here is not permission to
redistribute it.
