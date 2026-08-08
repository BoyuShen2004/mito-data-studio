import { describe, expect, it } from "vitest";
import {
  labelIdsTouchingRegion,
  protectHiddenRegionLabels,
  regionMembership,
} from "./regionOverlap";

/** 4x4 planes: `region` marks the ROI, `ids` the instance raster. */
const plane = (rows: number[][]) => Int32Array.from(rows.flat());
const roi = (rows: number[][]) => Uint8Array.from(rows.flat());

describe("labelIdsTouchingRegion", () => {
  it("keeps an instance that only partly overlaps the region", () => {
    const ids = plane([
      [3, 3, 0, 0],
      [3, 3, 0, 0],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
    ]);
    // The ROI covers only instance 3's bottom row.
    const region = roi([
      [0, 0, 0, 0],
      [1, 1, 0, 0],
      [1, 1, 0, 0],
      [0, 0, 0, 0],
    ]);

    expect([...labelIdsTouchingRegion(ids, region)]).toEqual([3]);
  });

  it("drops an instance that never enters the region", () => {
    const ids = plane([
      [0, 0, 4, 4],
      [0, 0, 4, 4],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
    ]);
    const region = roi([
      [1, 1, 0, 0],
      [1, 1, 0, 0],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
    ]);

    expect(labelIdsTouchingRegion(ids, region).size).toBe(0);
  });

  it("never treats background as an instance", () => {
    const ids = plane([
      [0, 0, 0, 0],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
    ]);
    const region = roi([
      [1, 1, 1, 1],
      [1, 1, 1, 1],
      [1, 1, 1, 1],
      [1, 1, 1, 1],
    ]);

    expect(labelIdsTouchingRegion(ids, region).size).toBe(0);
  });

  it("finds an instance that resumes after a gap in the region", () => {
    // The run-skipping walk must not lose id 5 when the ROI is interrupted.
    const ids = plane([
      [5, 5, 7, 7],
      [5, 5, 7, 7],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
    ]);
    const region = roi([
      [1, 0, 0, 1],
      [0, 1, 1, 0],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
    ]);

    expect([...labelIdsTouchingRegion(ids, region)].sort()).toEqual([5, 7]);
  });

  it("filters nothing when the decoded region does not match the plane", () => {
    const ids = plane([
      [1, 1, 1, 1],
      [1, 1, 1, 1],
      [1, 1, 1, 1],
      [1, 1, 1, 1],
    ]);

    expect(labelIdsTouchingRegion(ids, new Uint8Array(4)).size).toBe(0);
  });
});

describe("protectHiddenRegionLabels", () => {
  const region = roi([
    [1, 1, 0, 0],
    [1, 1, 0, 0],
    [0, 0, 0, 0],
    [0, 0, 0, 0],
  ]);

  it("blocks paint and erase over a label that does not touch the ROI", () => {
    const before = plane([
      [0, 0, 4, 4],
      [0, 0, 4, 4],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
    ]);
    const after = before.map((id) => (id === 4 ? 9 : id)) as Int32Array;
    after[3] = 0;

    expect(protectHiddenRegionLabels(before, after, region)).toBe(4);
    expect(after).toEqual(before);
  });

  it("allows empty outside pixels and the whole of a touching label", () => {
    const before = plane([
      [3, 0, 3, 0],
      [0, 0, 3, 0],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
    ]);
    const after = before.slice();
    after[2] = 8; // label 3 touches the ROI, so its outside part is editable.
    after[3] = 8; // outside background is also editable/stageable.

    expect(protectHiddenRegionLabels(before, after, region)).toBe(0);
    expect(after[2]).toBe(8);
    expect(after[3]).toBe(8);
  });

  it("edits a label the volume-wide set says is visible, even off-ROI here", () => {
    // Label 4 never touches the ROI *on this plane* — but it does somewhere
    // else in the volume, so Region only is drawing it and it must be editable.
    // Deciding per plane is what silently reverted strokes on visible labels.
    const before = plane([
      [0, 0, 4, 4],
      [0, 0, 4, 4],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
    ]);
    const after = before.slice();
    after[2] = 9;

    expect(protectHiddenRegionLabels(before, after, region, new Set([4]))).toBe(0);
    expect(after[2]).toBe(9);
  });

  it("still protects a label that is outside the region everywhere", () => {
    const before = plane([
      [0, 0, 4, 4],
      [0, 0, 4, 4],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
    ]);
    const after = before.slice();
    after[2] = 9;

    expect(protectHiddenRegionLabels(before, after, region, new Set([7]))).toBe(1);
    expect(after[2]).toBe(4);
  });
});

describe("regionMembership", () => {
  it("unions the server's volume-wide answer with what this plane shows", () => {
    // 1 and 2 reach the ROI on other planes; 9 is unsaved paint the server
    // has never seen, found by the per-plane scan.
    expect([...regionMembership(new Set([1, 2]), new Set([2, 9]))!].sort()).toEqual([1, 2, 9]);
  });

  it("returns a set the caller may keep growing", () => {
    const merged = regionMembership(new Set([1]), null)!;
    merged.add(5);
    expect([...merged].sort()).toEqual([1, 5]);
  });

  it("distinguishes 'nothing known yet' from 'nothing in the region'", () => {
    // null on both sides means the filter is not ready — callers must not read
    // that as "hide everything".
    expect(regionMembership(null, null)).toBeNull();
    expect(regionMembership(new Set<number>(), null)?.size).toBe(0);
  });
});
