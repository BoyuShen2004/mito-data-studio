import { describe, expect, it } from "vitest";
import { decodeRuns } from "./viewer";

describe("mask prediction RLE decoding", () => {
  it("preserves row-major geometry on a non-square plane", () => {
    const shape: [number, number] = [3, 5];
    const runs: [number, number][] = [[0, 7], [1, 3], [0, 5]];
    expect(runs.reduce((total, [, count]) => total + count, 0)).toBe(shape[0] * shape[1]);

    const decoded = decodeRuns(runs, shape[0] * shape[1]);
    expect(Array.from(decoded)).toEqual([
      0, 0, 0, 0, 0,
      0, 0, 1, 1, 1,
      0, 0, 0, 0, 0,
    ]);
  });
});
