import { describe, expect, it } from "vitest";

import {
  MAX_TRACKED_LABEL_ID,
  UsedLabelIds,
  nextFreshLabelId,
  usedLabelIdCapacity,
} from "./activeLabel";

const range = (from: number, to: number) =>
  Array.from({ length: to - from + 1 }, (_, i) => from + i);

describe("nextFreshLabelId", () => {
  it("fills the lowest hole rather than continuing past the maximum", () => {
    // 1-101 and 103-115 painted: 102 is the hole New must land on.
    const summaryIds = [...range(1, 101), ...range(103, 115)];
    expect(nextFreshLabelId({ summaryIds, trackParentIds: [], planes: [] })).toBe(102);
  });

  it("moves to the next free id only once the hole is occupied", () => {
    const summaryIds = [...range(1, 101), ...range(103, 115)];
    expect(
      nextFreshLabelId({ summaryIds: [...summaryIds, 102], trackParentIds: [], planes: [] }),
    ).toBe(116);
  });

  it("returns the same id for repeated New clicks that paint nothing", () => {
    const summaryIds = range(1, 5);
    const first = nextFreshLabelId({ summaryIds, trackParentIds: [], planes: [] });
    const second = nextFreshLabelId({ summaryIds, trackParentIds: [], planes: [] });
    // The old max+1 form counted the Active reservation and walked 6, 7, 8, …
    expect(first).toBe(6);
    expect(second).toBe(6);
  });

  it("counts unsaved planes, so a brushed-but-unsaved id is taken", () => {
    // 100 exists only in the pending buffer — nothing on the server knows it.
    const painted = Int32Array.from([0, 0, 100, 100, 0]);
    expect(
      nextFreshLabelId({ summaryIds: range(1, 99), trackParentIds: [], planes: [painted] }),
    ).toBe(101);
    // …and the hole below it still wins when there is one.
    const withHole = range(1, 99).filter((id) => id !== 42);
    expect(
      nextFreshLabelId({ summaryIds: withHole, trackParentIds: [], planes: [painted] }),
    ).toBe(42);
  });

  it("reserves Track parent classes that have no voxels yet", () => {
    // Queueing each fresh id as a parent is what makes New advance.
    const trackParentIds: number[] = [];
    for (const expected of [1, 2, 3]) {
      const next = nextFreshLabelId({ summaryIds: [], trackParentIds, planes: [] });
      expect(next).toBe(expected);
      trackParentIds.push(next);
    }
  });

  it("ignores an empty or absent plane", () => {
    expect(
      nextFreshLabelId({ summaryIds: [1], trackParentIds: [], planes: [null, undefined] }),
    ).toBe(2);
  });
});

describe("UsedLabelIds", () => {
  it("sizes from counts alone, never from the id values", () => {
    // Three ids can never push the answer past 4, however large they are.
    expect(usedLabelIdCapacity(3)).toBe(4);
    expect(usedLabelIdCapacity(1, 2, 0)).toBe(4);
    // Huge ids cost nothing: the bitset is bounded by how many were fed in.
    expect(
      nextFreshLabelId({ summaryIds: [5_000_000, 9_000_000], trackParentIds: [], planes: [] }),
    ).toBe(1);
  });

  it("caps the scan so a pathological id space cannot allocate unbounded", () => {
    expect(usedLabelIdCapacity(Number.MAX_SAFE_INTEGER)).toBe(MAX_TRACKED_LABEL_ID);
  });

  it("scans from 1 and skips ids outside its window", () => {
    const used = new UsedLabelIds(4);
    used.addAll(Int32Array.from([1, 2, 999999, -3, 0]));
    expect(used.smallestFree()).toBe(3);
  });
});
