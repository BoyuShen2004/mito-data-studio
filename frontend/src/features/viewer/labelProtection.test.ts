import { describe, expect, it } from "vitest";

import { protectLabelIds } from "./labelProtection";

describe("protectLabelIds", () => {
  it("blocks erasing and overwriting verified voxels", () => {
    const before = Int32Array.from([7, 7, 3, 0]);
    const after = Int32Array.from([0, 9, 3, 9]);
    expect(protectLabelIds(before, after, new Set([7]))).toBe(2);
    expect([...after]).toEqual([7, 7, 3, 9]);
  });

  it("blocks growing a verified id into new voxels", () => {
    const before = Int32Array.from([0, 3, 8]);
    const after = Int32Array.from([7, 7, 8]);
    expect(protectLabelIds(before, after, new Set([7]))).toBe(2);
    expect([...after]).toEqual([0, 3, 8]);
  });
});
