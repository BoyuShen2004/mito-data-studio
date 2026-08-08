import { describe, expect, it } from "vitest";
import { planLocalInterpolation } from "./localInterpolate";

function paintDisk(
  plane: Int32Array,
  h: number,
  w: number,
  cy: number,
  cx: number,
  r: number,
  label: number,
) {
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if ((y - cy) * (y - cy) + (x - cx) * (x - cx) <= r * r) {
        plane[y * w + x] = label;
      }
    }
  }
}

describe("planLocalInterpolation", () => {
  it("fills intermediates from unsaved endpoint planes", () => {
    const h = 16;
    const w = 16;
    const first = new Int32Array(h * w);
    const last = new Int32Array(h * w);
    for (let y = 4; y <= 7; y++) {
      for (let x = 4; x <= 7; x++) first[y * w + x] = 9;
    }
    for (let y = 6; y <= 9; y++) {
      for (let x = 6; x <= 9; x++) last[y * w + x] = 9;
    }
    const slices = planLocalInterpolation(first, last, h, w, 0, 4, 9);
    expect(slices.map((s) => s.index)).toEqual([1, 2, 3]);
    expect(slices.every((s) => s.mask.some(Boolean))).toBe(true);
  });

  it("returns nothing when a endpoint lacks the label", () => {
    const h = 8;
    const w = 8;
    const first = new Int32Array(h * w);
    const last = new Int32Array(h * w);
    first[3 * w + 3] = 2;
    expect(planLocalInterpolation(first, last, h, w, 0, 3, 2)).toEqual([]);
  });

  it("fills the object interior, not the bbox exterior (SDF sign)", () => {
    const h = 64;
    const w = 64;
    const first = new Int32Array(h * w);
    const last = new Int32Array(h * w);
    paintDisk(first, h, w, 32, 32, 10, 5);
    paintDisk(last, h, w, 32, 32, 14, 5);

    const slices = planLocalInterpolation(first, last, h, w, 0, 4, 5);
    expect(slices.length).toBe(3);
    const mid = slices[1].mask;

    // Center of the disk must be labeled (interior).
    expect(mid[32 * w + 32]).toBe(1);
    // Far corner of the padded bbox must stay empty (not the classic
    // inverted-SDF "filled square with mito hole").
    expect(mid[0 * w + 0]).toBe(0);
    expect(mid[2 * w + 2]).toBe(0);

    const labeled = mid.reduce((n, v) => n + (v ? 1 : 0), 0);
    // Mid radius ~12 → area ~π·12² ≈ 450; inverted bug fills bbox (~30²=900)
    // minus disk. Interior fill should be well under half the 64² plane.
    expect(labeled).toBeGreaterThan(200);
    expect(labeled).toBeLessThan(800);
  });
});
