import { describe, expect, it } from "vitest";
import {
  planeToSourceVoxel,
  renderedToPlane,
  sourceVoxelToPlane,
} from "./coordinates";

describe("Phase 14 coordinate contract", () => {
  it.each([
    ["z", [7, 11, 13], [13, 11, 7]],
    ["y", [7, 11, 13], [13, 7, 11]],
    ["x", [7, 11, 13], [11, 7, 13]],
  ] as const)("round-trips %s planes", (axis, voxel, plane) => {
    expect(sourceVoxelToPlane(axis, voxel)).toEqual(plane);
    expect(planeToSourceVoxel(axis, plane[2], plane[0], plane[1])).toEqual(voxel);
  });

  it("keeps CSS annotation coordinates independent of devicePixelRatio", () => {
    expect(renderedToPlane(42, 30, {
      zoom: 2,
      panX: 2,
      panY: 10,
      devicePixelRatio: 1,
    })).toEqual([20, 10]);
    expect(renderedToPlane(42, 30, {
      zoom: 2,
      panX: 2,
      panY: 10,
      devicePixelRatio: 3,
    })).toEqual([20, 10]);
  });

  it("rejects invalid transforms", () => {
    expect(() => renderedToPlane(0, 0, {
      zoom: 0,
      panX: 0,
      panY: 0,
      devicePixelRatio: 1,
    })).toThrow(RangeError);
  });
});
