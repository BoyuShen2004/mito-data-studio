# 07 — WEBKNOSSOS Interpolation Analysis

**Primary evidence (CODE):**
`frontend/javascripts/viewer/model/sagas/volume/volume_interpolation_saga.ts`
**DOC:** https://docs.webknossos.org/webknossos/volume_annotation/tools.html#volume-interpolation

## User workflow

1. Label active segment on slice A.
2. Move along the third axis of the active orthographic plane; label same segment on slice B.
3. Press **Interpolate** or shortcut **V**.
4. System fills intermediate slices for the **active cell ID** only.
5. Must be enabled via task/annotation restriction `volumeInterpolationAllowed`.
6. User must review; heuristics may err (**DOC** warning).

## Algorithm (**CODE** — verified)

1. Determine active viewport plane and third dimension from last label action.
2. Compute `interpolationDepth` between previous centroid and current position in **labeled mag**, respecting anisotropic mag clipping.
3. Constraints:
   - `MAXIMUM_INTERPOLATION_DEPTH = 100`
   - depth must be ≥ 2
4. Load bounding box of viewport ± depth via `api.data.getDataForBoundingBox`.
5. Build binary masks for activeCellId on first and last slices (supports BigUint64 IDs by early convert to Float32 masks).
6. Compute **signed distance transform** of each mask:
   - `distance-transform` package on mask and inverted mask
   - combine via `absMax` → outside positive, inside negative
7. For each intermediate slice offset `k = targetOffsetW / interpolationDepth`:
   - `weightedAverage = firstDist*(1-k) + lastDist*k`
   - draw where `weightedAverage < 0`
8. Apply via `labelWithVoxelBuffer2D` with current **overwriteMode**.
9. `finishAnnotationStrokeAction` — one stroke for undo.
10. `registerLabelPointAction(position)` so chained interpolations advance the “last label” reference.

## Properties

| Property | Behavior |
|---|---|
| Label-specific | Yes — active segment only |
| Direction | Along viewport third axis (XY→Z, etc.) |
| Holes / components | Implicit via SDF; topology can change between ends |
| Anisotropy | Via mag-aware depth calculation |
| Preview | Not a separate modal in this saga — applies stroke directly (**CODE**); DOC says review after |
| Collision | Governed by overwrite mode |
| Undo | Single finished stroke |
| Gating | `volumeInterpolationAllowed` |

## Reuse decision for mito

**Preferred:** port algorithm (SDF + linear blend of distances) into mito’s label volume pipeline — either:

- TypeScript port adapted to Canvas/RLE slice model, or
- Python service implementing identical math with tests against WK fixtures.

Record as **ported algorithm** under AGPL inspiration; if copying the saga file itself → AGPL compliance. Independent reimplementation of SDF interpolation is scientifically standard; still cite WK behavior and match edge cases (depth limits, active-id only, overwrite modes).
