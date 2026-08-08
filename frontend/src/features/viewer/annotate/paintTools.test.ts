import { describe, expect, it } from "vitest";
import {
  CONTEXT_MENU_LAYOUT,
  CONTEXT_MENU_TOOLS,
  CROSSHAIR_CURSOR_TOOLS,
  CUSTOM_OVERLAY_CURSOR_TOOLS,
  canvasCursorForTool,
  type PaintTool,
} from "./paintTools";

const ALL_TOOLS: PaintTool[] = [
  "select", "brush", "eraser", "box_eraser", "point_mask", "box_mask",
  "boundary", "seeds", "interpolate", "flood_fill", "split_3d", "merge", "delete",
];

describe("canvas cursor policy", () => {
  it("covers every paint tool exactly once", () => {
    const covered = new Set<PaintTool>([
      "select", ...CUSTOM_OVERLAY_CURSOR_TOOLS, ...CROSSHAIR_CURSOR_TOOLS,
    ]);
    expect([...covered].sort()).toEqual([...ALL_TOOLS].sort());
  });

  it("uses custom overlay for brush, eraser, box, and point-prompt tools", () => {
    for (const tool of [
      "brush", "eraser", "box_mask", "box_eraser", "point_mask", "boundary",
    ] as const) {
      expect(canvasCursorForTool(tool, { editable: true, swapped: false })).toBe("none");
    }
  });

  it("uses Split-style CSS crosshair for all other annotate tools", () => {
    for (const tool of CROSSHAIR_CURSOR_TOOLS) {
      expect(canvasCursorForTool(tool, { editable: true, swapped: false })).toBe("crosshair");
    }
  });

  it("select uses pointer; view-only uses default", () => {
    expect(canvasCursorForTool("select", { editable: true, swapped: false })).toBe("pointer");
    expect(canvasCursorForTool("flood_fill", { editable: false, swapped: false })).toBe("pointer");
    expect(canvasCursorForTool("brush", { editable: false, swapped: true })).toBe("default");
  });
});

describe("canvas context menu tools", () => {
  it("contains the complete annotate tool strip in order", () => {
    expect(CONTEXT_MENU_TOOLS.map(([, label]) => label)).toEqual([
      "Select", "Brush", "Erase", "Box Erase", "Box Mask", "Point Mask",
      "Boundary", "Seeds", "Interpolate", "Flood fill", "Split", "Merge", "Delete",
    ]);
  });

  it("lays the menu out as two columns of related pairs", () => {
    // Read row by row with Cancel occupying the first cell. `null` is the
    // empty half of a row, which is what keeps each pair on one line.
    expect(CONTEXT_MENU_LAYOUT).toEqual([
      "select",
      "brush", "eraser",
      "box_mask", "box_eraser",
      "point_mask", "boundary",
      "seeds", null,
      "interpolate", "flood_fill",
      "split_3d", "merge",
      "delete", null,
    ]);
  });

  it("offers every tool exactly once, gaps aside", () => {
    const listed = CONTEXT_MENU_LAYOUT.filter((tool): tool is PaintTool => tool != null);
    expect(new Set(listed).size).toBe(listed.length);
    expect(new Set(listed)).toEqual(new Set(CONTEXT_MENU_TOOLS.map(([tool]) => tool)));
  });
});
