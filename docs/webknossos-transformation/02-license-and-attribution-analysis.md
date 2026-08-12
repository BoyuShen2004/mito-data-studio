# 02 — License and Attribution Analysis

**Disclaimer:** This is an engineering compliance plan, not legal advice. The user has stated they will satisfy WEBKNOSSOS license obligations. Claude Code must still record every copy/adapt/port.

## License inventory

| Component | Location | License | SPDX |
|---|---|---|---|
| WEBKNOSSOS main application | github.com/scalableminds/webknossos | GNU Affero GPL v3 | AGPL-3.0 |
| WEBKNOSSOS datastore | `webknossos-datastore/LICENSE` | AGPL-3.0 | AGPL-3.0 |
| `webknossos` Python package | `/home/weidf/shenb/webknossos-libs/webknossos/LICENSE` | AGPL-3.0 | AGPL-3.0 |
| `cluster_tools` | `/home/weidf/shenb/webknossos-libs/cluster_tools/LICENSE` | MIT | MIT |
| mito-data-studio | no LICENSE file found at audit | **Unknown / unset** | — |
| EfficientSAM / SAM2 vendored | `mito-data-studio/vendor/` | Per upstream model licenses | must record |

## AGPL implications (engineering requirements)

If mito-data-studio **copies, modifies, or links** AGPL-covered WEBKNOSSOS code (main app, datastore, or `webknossos` Python package) into a network-accessible service:

1. Preserve copyright and license notices.
2. Mark modified files.
3. Offer Corresponding Source to users of the network service (AGPL §13).
4. Distribute under AGPL-compatible terms for the combined work (or keep AGPL components as separate processes with clear boundaries — still document carefully).
5. Maintain a source/attribution register (see `26-license-compliance-plan.md`).

MIT `cluster_tools` may be reused with attribution under MIT terms.

## Recommended compliance posture for this transformation

| Approach | When to use |
|---|---|
| **AGPL whole-product** | If substantial WK frontend/backend/datastore code is adapted into mito-data-studio and served as a network app — simplest legally coherent path: dual-publish or AGPL the resulting platform and provide source offer |
| **Process isolation** | Run WK datastore / tracingstore as separate AGPL services; mito talks via HTTP — still disclose if modified; client may still be AGPL if linking `webknossos` Python |
| **Independent reimplementation** | Reproduce *behavior* from docs + observed algorithms without copying code — still cite inspiration; avoid line-level ports of AGPL files unless willing to AGPL |
| **Permissive islands** | Prefer MIT `cluster_tools`, TensorStore, Zarr, scikit-image for storage/compute where AGPL is undesirable |

**User instruction overrides aversion:** do **not** choose a technically inferior design merely to avoid AGPL when compliant reuse is intended. Prefer recording obligations over rejecting good designs.

## Attribution register template (mandatory for Claude Code)

For every reused artifact:

| Field | Example |
|---|---|
| Source repo | scalableminds/webknossos |
| Path | `frontend/.../volume_interpolation_saga.ts` |
| License | AGPL-3.0 |
| Relationship | copied / modified / ported / independently reimplemented / API-integrated |
| Destination path | `frontend/.../interpolation.ts` |
| Notices preserved | yes/no |
| Source disclosure notes | … |

## mito-data-studio license gap

Before production deployment, Claude Code must:

1. Confirm intended license for mito-data-studio with the user.
2. If AGPL components are incorporated, align the product license or document multi-license boundaries.
3. Generate `NOTICE`, `LICENSE`, and attribution files under `docs/webknossos-transformation/attribution/` (or repo root).
