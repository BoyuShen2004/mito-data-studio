# Third-Party Notices

Third-party software distributed with, vendored into, or reused by
mito-data-studio, with its original licence.

**Status of this file:** authoritative and current as of 2026-08-01.
Every entry must be added *before* the corresponding code is committed.
The machine-readable companion is
[`docs/webknossos-transformation/attribution/REGISTER.md`](docs/webknossos-transformation/attribution/REGISTER.md).

The project root `LICENSE` grants no permission for first-party code. Third-party
components remain governed by the licenses recorded here and shipped beside
vendored material.

---

## 1. Vendored components (present in this repository)

| Component | Path | Upstream | Licence | Relationship |
|---|---|---|---|---|
| EfficientSAM | `vendor/efficient_sam/` | labelmeai/efficient-sam `onnx-models-20231225` (`6aebcba09318c4dfe2f9560f7a3f8c42d8b01657`) | Apache-2.0 (`vendor/efficient_sam/LICENSE`) | 2 ONNX weight files, exact release bytes, no source |
| SAM 2 | `vendor/sam2/` | facebookresearch/sam2 (`2b90b9f5ceec907a1c18123530e92e794ad901a4`) | Apache-2.0 (`vendor/sam2/LICENSE`) | 22 source/config files matching the pinned tree + official SAM 2.1 checkpoint |

### Verified state as of 2026-08-01

Checked directly, not inferred:

| Question | `vendor/efficient_sam` | `vendor/sam2` |
|---|---|---|
| `LICENSE`/`COPYING` present? | **Yes** — Apache-2.0 | **Yes** — Apache-2.0 |
| Upstream commit pinned? | **Yes** — tag and commit above | **Yes** — commit above |
| Tracked in git? | Yes — 2 files | Yes — 25 files |
| Source code or weights only? | Weights only (`.onnx`) | **Source** (21 `.py`) + `.pt` checkpoint |
| Copyright headers present? | n/a (binary) | **Yes** — `Copyright (c) Meta Platforms, Inc. and affiliates.` |

The EfficientSAM files were downloaded again from the named GitHub release and
matched the repository bytes exactly (`4cacbb23…` encoder, `4727baf2…` decoder).
The SAM2 source/config files matched every corresponding file at the pinned
official commit, and the checkpoint downloaded from the official URL matched
`2647878d…`. The two upstream Apache-2.0 texts are byte-identical and are now
carried in-tree beside each component.

---

## 2. WEBKNOSSOS

WEBKNOSSOS is used as an **architecture and behavioural reference**. Behaviour
is reproduced from documentation and observed algorithms; describing what a
system does carries no licence obligation.

| Component | Upstream | Licence |
|---|---|---|
| WEBKNOSSOS main application | github.com/scalableminds/webknossos | **AGPL-3.0** |
| WEBKNOSSOS datastore / tracingstore | same repo | **AGPL-3.0** |
| `webknossos` Python package | github.com/scalableminds/webknossos-libs | **AGPL-3.0** |
| `cluster_tools` | same repo | **MIT** |

### Copied or derived WEBKNOSSOS source

**None.** No WEBKNOSSOS source has been copied, ported, or adapted into this
repository as of 2026-08-01.

A read-only reference clone is kept **outside** this repository at
`/home/weidf/shenb/external-research/webknossos` (commit `a24aecc6f`). It is
never vendored, never committed, and nothing is copied from it without an entry
in this file first.

### If that changes

Per decision **D3**, direct reuse is permitted where it delivers substantial
engineering benefit *and* provenance is documented. Before any such code is
committed, all of the following must already be true:

1. An entry exists in this file **and** in `attribution/REGISTER.md`, naming
   the upstream path and commit.
2. Original copyright headers are preserved **verbatim** — never removed,
   never altered.
3. The file is **not** relabelled under any other licence. AGPL code is
   labelled AGPL.
4. The code lives under a clearly marked, isolated path (proposed:
   `third_party/webknossos/`), never interleaved into mito modules.
5. The relicensing consequence below has been put to the repository owner and
   accepted.

### Relicensing exposure

AGPL-3.0 is a strong copyleft licence with a network clause (§13). Copying
AGPL-covered source into a network-served application generally obliges the
**combined work** to be offered under AGPL-compatible terms, and requires that
users interacting with it over a network be offered the Corresponding Source.

Practically, for this repository:

- Copying WEBKNOSSOS source into the served product is a **repository-wide**
  licensing event, not a per-file one.
- Running an unmodified AGPL component as a **separate process** behind an HTTP
  boundary is a materially different (and narrower) exposure than linking or
  copying its source into mito modules.
- Importing the `webknossos` **Python package** into mito's own process is
  closer to the former than the latter.

This is an engineering summary, not legal advice. The repository owner should
obtain their own review before distribution or public deployment.

---

## 3. Python and JavaScript dependencies

Ordinary dependencies declared in `requirements-release.txt` and
`frontend/package-lock.json` retain their own licences. The complete locked,
installed inventory and the audit method are recorded in
`docs/releases/v1.1.0-license-provenance.md`; run
`ops/release/audit_dependency_licenses.py` from the release venv after
`npm ci` to reproduce the machine-readable inventory. The v1.1.0 audit covers
55 Python distributions and 181 JavaScript packages and found no package with
missing licence metadata or licence file.

The inventory is not uniformly permissive. In particular it records psycopg
(LGPL-3.0-only), tqdm (MPL-2.0 and MIT), caniuse-lite (CC-BY-4.0), NVIDIA CUDA
runtime wheels (NVIDIA proprietary terms), and the notices embedded in the
SciPy binary wheel (including GCC Runtime Library Exception and libquadmath).
Those original notices must remain available with any redistributed runtime.

**Not currently a dependency:** the `webknossos` PyPI package (AGPL-3.0).
Adding it is permitted under D3 but is a licensing event — record it here first.
