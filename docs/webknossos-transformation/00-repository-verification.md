# 00 — Repository Verification

**Date:** 2026-07-27
**Mode:** read-only inspection
**Status:** verified

## Local path inspected

```text
/home/weidf/shenb/webknossos-libs
```

## Git identity

| Field | Value |
|---|---|
| Upstream remote | `https://github.com/scalableminds/webknossos-libs.git` |
| Repository identity | **scalableminds/webknossos-libs** (Python client libs monorepo) |
| Not present | **scalableminds/webknossos** (main Scala/Play + React application) |
| Branch | `master` (up to date with `origin/master`) |
| HEAD commit | `0419d102710e428f245cf2d520b7a0ee33e1d4a5` — `fixed links in docs` |
| Latest tag near HEAD | `v3.5.6` (webknossos Python package; pyproject uses dunamai `0.0.0` placeholder) |
| Working tree | clean (no local modifications) |
| Top-level purpose | Python API (`webknossos/`), cluster executors (`cluster_tools/`), combined docs (`docs/`) |

## Classification

| Hypothesis | Result |
|---|---|
| Main WEBKNOSSOS application (`scalableminds/webknossos`) | **No** — not cloned locally under `/home/weidf/shenb` |
| `webknossos-libs` | **Yes** — official upstream clone |
| Fork / partial export | **No** — clean official remote |
| Other WEBKNOSSOS-related dirs | `/home/weidf/shenb/wk_data` (data, not source); `/home/weidf/temp/webknossos_downsampled` (data artifacts) |

## Major local components (`webknossos-libs`)

| Path | Role | License |
|---|---|---|
| `webknossos/` | Dataset I/O (Zarr3/Zarr/WKW/N5/NG), annotations, REST client, CLI | **AGPL-3.0** |
| `cluster_tools/` | Slurm/K8s/Dask/multiprocessing executors | **MIT** |
| `docs/` | MkDocs site (can pull main-app docs into `docs/wk-repo`) | — |

## Application under improvement

```text
/home/weidf/shenb/mito-data-studio
```

| Field | Value |
|---|---|
| Remote | `https://github.com/BoyuShen2004/mito-data-studio.git` |
| Branch | `main` |
| HEAD (at audit) | `83b547c` |
| Working tree | dirty (in-progress hard-cases/people/submit-loop work; not modified by this research) |

## Implication for Claude Code

Full source-level analysis of the **main application** requires cloning:

```text
/home/weidf/shenb/external-research/webknossos
```

from `https://github.com/scalableminds/webknossos` (do **not** clone inside `mito-data-studio`).

This Cursor research used:

1. Local `webknossos-libs` (full tree read)
2. Official docs at https://docs.webknossos.org/
3. Raw GitHub source of `scalableminds/webknossos` (master tree + key files via API/raw)
4. Latest release tag inspected: **26.08.0** (2026-07-23)

## Evidence

- `git remote -v`, `git rev-parse HEAD` on `/home/weidf/shenb/webknossos-libs`
- GitHub API `GET /repos/scalableminds/webknossos` → language TypeScript, license AGPL-3.0, modules: `app/`, `frontend/`, `webknossos-datastore/`, `webknossos-tracingstore/`, `fossildb/`
