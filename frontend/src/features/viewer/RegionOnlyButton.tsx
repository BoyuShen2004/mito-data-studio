import type { OverwriteMode } from "../../api/viewer";
import type { AxisControls } from "./AnnotationCanvas";

/**
 * The Region-only display gate plus its Overwrite policy, as one topbar group.
 *
 * Overwrite means the same thing here as it does for Interpolate and Flood fill
 * (`tool-overwrite-control` in `AnnotateToolChrome`), so the markup, the class
 * names and the wording are deliberately identical: it decides whether an edit
 * made on empty space outside the region may replace a baseline voxel or only
 * fill empty ones. Region-only display remains strict: outside edits never
 * exempt a non-touching label from the visibility filter. Leaving Region only
 * is when staged edits are presented against baseline labels. See
 * `outsideRegionEdits.ts` for the model.
 */
export default function RegionOnlyButton({ controls }: { controls: AxisControls }) {
  if (!controls.hasRegion) return null;

  return (
    <>
      <button
        type="button"
        className={controls.regionOnly ? "" : "secondary"}
        aria-pressed={controls.regionOnly}
        title="Show only mitochondria that touch the Region — each one whole. Outside edits are staged but do not bypass this filter."
        onClick={() => controls.changeRegionOnly(!controls.regionOnly)}
      >
        Region only
      </button>
      <span className="tool-overwrite-control region-overwrite-control">
        <label className="muted" htmlFor="region-overwrite-policy">
          Overwrite
        </label>
        <select
          id="region-overwrite-policy"
          className="tool-overwrite-select"
          value={controls.regionOverwriteMode}
          title="How edits made outside the Region are presented when Region only is switched off: fill only empty voxels, or replace existing labels too."
          onChange={(e) =>
            controls.changeRegionOverwriteMode(e.target.value as OverwriteMode)
          }
        >
          <option value="overwrite_empty">Empty voxels only</option>
          <option value="overwrite_all">All voxels</option>
        </select>
      </span>
    </>
  );
}
