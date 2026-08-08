import { beforeEach, describe, expect, it } from "vitest";
import {
  BRUSH_MAX_SIZE,
  BRUSH_MIN_SIZE,
  CURSOR_STYLES,
  DEFAULT_CURSOR_STYLE,
  brushRadius,
  isBrushCursorStyle,
  loadBrushCursorStyle,
  saveBrushCursorStyle,
} from "./brushCursor";

/** The footprint `paintAt` fills for a given size — the same arithmetic, so a
 * change to one without the other fails here. */
function footprint(size: number): number {
  const radius = brushRadius(size);
  const reach = Math.floor(radius);
  const r2 = radius * radius;
  let count = 0;
  for (let dy = -reach; dy <= reach; dy++) {
    for (let dx = -reach; dx <= reach; dx++) {
      if (dx * dx + dy * dy <= r2) count += 1;
    }
  }
  return count;
}

describe("brush footprint", () => {
  it("paints exactly one pixel at the minimum size", () => {
    // The bug this pins: read as a radius, size 1 covered a five-pixel plus
    // and single-voxel corrections were impossible.
    expect(BRUSH_MIN_SIZE).toBe(1);
    expect(footprint(1)).toBe(1);
  });

  it("treats the size as the footprint width, not a radius", () => {
    expect(footprint(3)).toBe(9); // the full 3x3 block
    expect(brushRadius(4)).toBe(2);
    expect(brushRadius(1)).toBe(0.5);
  });

  it("grows monotonically across the whole slider range", () => {
    let previous = 0;
    for (let size = BRUSH_MIN_SIZE; size <= BRUSH_MAX_SIZE; size++) {
      const area = footprint(size);
      expect(area).toBeGreaterThanOrEqual(previous);
      previous = area;
    }
    expect(previous).toBeGreaterThan(footprint(1));
  });

  it("never returns a degenerate radius for a nonsense size", () => {
    expect(brushRadius(0)).toBe(0.5);
    expect(brushRadius(-3)).toBe(0.5);
  });
});

describe("cursor style preference", () => {
  beforeEach(() => window.localStorage.clear());

  it("offers exactly five styles, defaulting to the familiar disc", () => {
    expect(CURSOR_STYLES).toHaveLength(5);
    expect(DEFAULT_CURSOR_STYLE).toBe("disc");
    expect(CURSOR_STYLES[0].value).toBe("disc");
    expect(new Set(CURSOR_STYLES.map((s) => s.value)).size).toBe(5);
  });

  it("round-trips a choice through localStorage", () => {
    saveBrushCursorStyle("brackets");
    expect(loadBrushCursorStyle()).toBe("brackets");
  });

  it("falls back to the default for missing or junk values", () => {
    expect(loadBrushCursorStyle()).toBe(DEFAULT_CURSOR_STYLE);
    window.localStorage.setItem("mito.brush-cursor-style", "hologram");
    expect(loadBrushCursorStyle()).toBe(DEFAULT_CURSOR_STYLE);
    expect(isBrushCursorStyle("hologram")).toBe(false);
    expect(isBrushCursorStyle("dashed")).toBe(true);
  });
});
