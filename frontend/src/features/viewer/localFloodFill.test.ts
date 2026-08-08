import { describe, expect, it } from "vitest";

import { floodFillBlock, floodFillPlane } from "./localFloodFill";

describe("localFloodFill", () => {
  it("fills a 2-D background pocket without leaking diagonally", () => {
    // 0 0 1
    // 0 0 1
    // 1 1 1
    const plane = new Int32Array([0, 0, 1, 0, 0, 1, 1, 1, 1]);
    const changed = floodFillPlane(plane, 3, 3, 0, 0, 7, "overwrite_empty");
    expect(changed).toBe(4);
    expect(Array.from(plane)).toEqual([7, 7, 1, 7, 7, 1, 1, 1, 1]);
  });

  it("overwrite_empty does not replace an existing segment", () => {
    const plane = new Int32Array([5, 5, 0, 5, 5, 0]);
    const changed = floodFillPlane(plane, 2, 3, 0, 0, 9, "overwrite_empty");
    expect(changed).toBe(0);
    expect(Array.from(plane)).toEqual([5, 5, 0, 5, 5, 0]);
  });

  it("overwrite_all recolors the seed's connected component", () => {
    const plane = new Int32Array([5, 5, 0, 5, 0, 0]);
    const changed = floodFillPlane(plane, 2, 3, 0, 0, 9, "overwrite_all");
    expect(changed).toBe(3);
    expect(Array.from(plane)).toEqual([9, 9, 0, 9, 0, 0]);
  });

  it("limited 3-D fill crosses z with 6-connectivity", () => {
    const d = 2;
    const h = 2;
    const w = 2;
    const block = new Int32Array(d * h * w); // all 0
    block[1] = 1; // (z0,y0,x1) wall
    const changed = floodFillBlock(block, d, h, w, 0, 0, 0, 3, "overwrite_empty");
    expect(changed).toBe(7);
    expect(block[1]).toBe(1);
    expect(block[0]).toBe(3);
    expect(block[4]).toBe(3);
  });
});
