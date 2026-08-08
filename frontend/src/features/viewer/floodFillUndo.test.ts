import { describe, expect, it } from "vitest";
import { SliceHistory } from "./sliceHistory";
import { floodFillPlane } from "./localFloodFill";

describe("flood fill undo", () => {
  it("records a compound undo step for a single-slice fill", () => {
    const history = new SliceHistory();
    const h = 5;
    const w = 5;
    const before = new Int32Array(h * w);
    const after = before.slice();
    floodFillPlane(after, h, w, 2, 2, 7, "overwrite_empty");
    expect(before.some((v, i) => v !== after[i])).toBe(true);

    expect(
      history.recordCompound([{ index: 3, before, after }]),
    ).toBe(true);
    expect(history.undoCount).toBe(1);

    const live = after.slice();
    const undone = history.undo(live);
    expect(undone?.kind).toBe("compound");
    if (undone?.kind !== "compound") throw new Error("expected compound");
    expect([...undone.slices[0].before]).toEqual([...before]);
  });
});
