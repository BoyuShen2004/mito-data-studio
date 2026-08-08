import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearRegionIndexCache,
  loadRegionIndex,
  nearestRegionIndex,
} from "./regionIndex";
import type { RegionIndex } from "../../api/viewer";

const index = (indices: number[]): RegionIndex => ({
  axis: "z",
  length: indices.length,
  indices,
});

describe("nearestRegionIndex", () => {
  it("finds the closest region-bearing plane in either direction", () => {
    expect(nearestRegionIndex([10, 11, 12], 30)).toBe(12);
    expect(nearestRegionIndex([10, 11, 12], 3)).toBe(10);
    expect(nearestRegionIndex([2, 40], 30)).toBe(40);
  });

  it("does nothing when the current plane already has region", () => {
    expect(nearestRegionIndex([4, 5, 6], 5)).toBeNull();
    expect(nearestRegionIndex([4, 5, 6], 4)).toBeNull();
  });

  it("does nothing when no plane has region", () => {
    expect(nearestRegionIndex([], 7)).toBeNull();
  });

  it("breaks a tie deterministically, toward the lower index", () => {
    // Exactly between two ROI blocks: always step back, never wobble.
    expect(nearestRegionIndex([4, 10], 7)).toBe(4);
    expect(nearestRegionIndex([10, 4], 7)).toBe(4);
  });
});

describe("loadRegionIndex", () => {
  beforeEach(() => {
    clearRegionIndexCache();
    window.sessionStorage.clear();
  });

  it("asks the server once per volume and axis", async () => {
    const fetcher = vi.fn(async () => index([1, 2]));

    expect(await loadRegionIndex(7, "z", fetcher)).toEqual([1, 2]);
    expect(await loadRegionIndex(7, "z", fetcher)).toEqual([1, 2]);

    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("de-dupes concurrent clicks into one request", async () => {
    const fetcher = vi.fn(async () => index([5]));

    const [a, b] = await Promise.all([
      loadRegionIndex(7, "z", fetcher),
      loadRegionIndex(7, "z", fetcher),
    ]);

    expect(a).toEqual([5]);
    expect(b).toEqual([5]);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("keeps each axis and each volume apart", async () => {
    const fetcher = vi.fn(async (_volumeId: number, axis: string) =>
      index(axis === "z" ? [1] : [9]),
    );

    expect(await loadRegionIndex(7, "z", fetcher)).toEqual([1]);
    expect(await loadRegionIndex(7, "y", fetcher)).toEqual([9]);
    expect(await loadRegionIndex(8, "z", fetcher)).toEqual([1]);

    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("survives a page navigation through sessionStorage", async () => {
    const fetcher = vi.fn(async () => index([3, 4]));
    await loadRegionIndex(7, "z", fetcher);

    clearRegionIndexCache(); // ...which also clears sessionStorage.
    await loadRegionIndex(7, "z", fetcher);
    expect(fetcher).toHaveBeenCalledTimes(2);

    // A fresh page in the same tab: only the in-memory map is gone.
    window.sessionStorage.setItem("mito.region-index.7.z", "[3,4]");
    expect(await loadRegionIndex(7, "z", fetcher)).toEqual([3, 4]);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("does not cache a failure — the next click retries", async () => {
    const fetcher = vi
      .fn<() => Promise<RegionIndex>>()
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(index([6]));

    await expect(loadRegionIndex(7, "z", fetcher)).rejects.toThrow("boom");
    expect(await loadRegionIndex(7, "z", fetcher)).toEqual([6]);
  });
});
