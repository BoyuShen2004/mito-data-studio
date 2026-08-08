/** Annotate-only paint modes (Cellable-aligned shortcuts). */
export type PaintTool =
  | "select"
  | "brush"
  | "eraser"
  | "box_eraser"
  | "point_mask"
  | "box_mask"
  | "boundary"
  | "seeds"
  | "interpolate"
  | "flood_fill"
  | "split_3d"
  | "merge"
  | "delete";

export const AI_POINT_TOOLS: PaintTool[] = ["point_mask", "boundary"];
export const AI_PREVIEW_TOOLS: PaintTool[] = ["point_mask", "box_mask", "boundary"];

export const CONTEXT_MENU_TOOLS: readonly [PaintTool, string][] = [
  ["select", "Select"],
  ["brush", "Brush"],
  ["eraser", "Erase"],
  ["box_eraser", "Box Erase"],
  ["box_mask", "Box Mask"],
  ["point_mask", "Point Mask"],
  ["boundary", "Boundary"],
  ["seeds", "Seeds"],
  ["interpolate", "Interpolate"],
  ["flood_fill", "Flood fill"],
  ["split_3d", "Split"],
  ["merge", "Merge"],
  ["delete", "Delete"],
];

/**
 * The right-click menu's two-column reading order. `null` is a cell the row
 * above/left spills into, so the pairs stay meaningful:
 *
 *   Cancel      | Select
 *   Brush       | Erase          <- paint pair
 *   Box Mask    | Box Erase      <- box pair
 *   Point Mask  | Boundary       <- AI point pair
 *   Seeds       |
 *   Interpolate | Flood fill     <- bulk fill pair
 *   Split       | Merge          <- topology pair
 *   Delete      |
 *
 * Column-major grouping (what a plain `flex-direction: column` menu gives)
 * would have made the menu twice as tall as the canvas it covers; laying it
 * out in pairs is what keeps it short *and* readable. `Cancel` is not a tool,
 * so it is not in this list — it occupies the first cell in the markup.
 */
export const CONTEXT_MENU_LAYOUT: readonly (PaintTool | null)[] = [
  "select",
  "brush",
  "eraser",
  "box_mask",
  "box_eraser",
  "point_mask",
  "boundary",
  "seeds",
  null,
  "interpolate",
  "flood_fill",
  "split_3d",
  "merge",
  "delete",
  null,
];

/** Hide the OS cursor — custom overlay is drawn on the cursor layer. */
export const CUSTOM_OVERLAY_CURSOR_TOOLS: readonly PaintTool[] = [
  "brush",
  "eraser",
  "box_mask",
  "box_eraser",
  "point_mask",
  "boundary",
];

/** Native CSS crosshair on the canvas (Split-style). */
export const CROSSHAIR_CURSOR_TOOLS: readonly PaintTool[] = [
  "seeds",
  "interpolate",
  "flood_fill",
  "split_3d",
  "merge",
  "delete",
];

export function canvasCursorForTool(
  paintTool: PaintTool,
  opts: { editable: boolean; swapped: boolean },
): string {
  // View / share / hard-case read-only: same eyedropper affordance as Select.
  if (!opts.editable && !opts.swapped) return "pointer";
  if (!opts.editable || opts.swapped) return "default";
  if (paintTool === "select") return "pointer";
  if (CUSTOM_OVERLAY_CURSOR_TOOLS.includes(paintTool)) return "none";
  if (CROSSHAIR_CURSOR_TOOLS.includes(paintTool)) return "crosshair";
  return "crosshair";
}

export function usesCustomOverlayCursor(paintTool: PaintTool): boolean {
  return CUSTOM_OVERLAY_CURSOR_TOOLS.includes(paintTool);
}
