import { describe, expect, it } from "vitest";

import { applyProtectedLabelPlanDelta, protectLabelIds } from "./labelProtection";

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

describe("applyProtectedLabelPlanDelta", () => {
  it("restores an unchanged verified server neighbour missing from the client plane", () => {
    const client = Int32Array.from([0, 3, 0]);
    const serverBefore = Int32Array.from([7, 3, 0]);
    const serverAfter = Int32Array.from([7, 8, 0]);

    const result = applyProtectedLabelPlanDelta(
      client, serverBefore, serverAfter, new Set([7]),
    );

    expect([...result.after]).toEqual([7, 8, 0]);
    expect(result.blocked).toBe(0);
  });

  it("applies only the target remap and leaves verified neighbours untouched", () => {
    const client = Int32Array.from([7, 9, 4, 7]);
    const serverBefore = Int32Array.from([7, 9, 4, 7]);
    const serverAfter = Int32Array.from([7, 5, 4, 7]);

    const result = applyProtectedLabelPlanDelta(
      client, serverBefore, serverAfter, new Set([7]),
    );

    expect([...result.after]).toEqual([7, 5, 4, 7]);
    expect(result.blocked).toBe(0);
  });

  it("refuses a tool delta that removes or grows a verified id", () => {
    const result = applyProtectedLabelPlanDelta(
      Int32Array.from([7, 0]),
      Int32Array.from([7, 0]),
      Int32Array.from([0, 7]),
      new Set([7]),
    );

    expect([...result.after]).toEqual([7, 0]);
    expect(result.blocked).toBe(2);
  });
});
