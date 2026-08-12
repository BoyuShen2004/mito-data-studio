import { describe, expect, it } from "vitest";
import {
  historyEntryLimit,
  MAX_SLICE_UNDO,
  SliceHistory,
} from "./sliceHistory";
import { PendingSliceBuffer } from "./pendingSliceBuffer";

describe("SliceHistory", () => {
  it("keeps small history and caps a large plane by bytes", () => {
    expect(historyEntryLimit(1024 * 1024)).toBe(MAX_SLICE_UNDO);
    expect(historyEntryLimit(3885 * 4544 * 4)).toBe(2);
  });
  it("does not record a no-op stroke", () => {
    const history = new SliceHistory();
    const ids = new Int32Array([1, 2, 3]);
    history.beginStroke(ids);
    expect(history.commitStroke(ids)).toBe(false);
    expect(history.undoCount).toBe(0);
  });

  it("records a stroke only after pixels change", () => {
    const history = new SliceHistory();
    const ids = new Int32Array([1, 2, 3]);
    history.beginStroke(ids);
    ids[1] = 9;
    expect(history.commitStroke(ids)).toBe(true);
    expect(history.undoCount).toBe(1);
    expect(history.redoCount).toBe(0);
  });

  it("undo and redo round-trip a committed change", () => {
    const history = new SliceHistory();
    const ids = new Int32Array([0, 0, 0]);
    history.beginStroke(ids);
    ids[0] = 5;
    history.commitStroke(ids);

    const undone = history.undo(ids);
    expect(undone?.kind).toBe("slice");
    if (undone?.kind !== "slice") throw new Error("expected slice");
    expect([...undone.raster]).toEqual([0, 0, 0]);
    expect(history.undoCount).toBe(0);
    expect(history.redoCount).toBe(1);

    const redone = history.redo(undone.raster);
    expect(redone?.kind).toBe("slice");
    if (redone?.kind !== "slice") throw new Error("expected slice");
    expect([...redone.raster]).toEqual([5, 0, 0]);
    expect(history.undoCount).toBe(1);
    expect(history.redoCount).toBe(0);
  });

  it("keeps parked history intact when the live stack is popped", () => {
    const history = new SliceHistory();
    const a = new Int32Array([1]);
    history.beginStroke(a);
    a[0] = 2;
    history.commitStroke(a);
    history.stash(0);

    // Arrive on another slice with its own history, then undo there.
    history.restore(1);
    const b = new Int32Array([7]);
    history.beginStroke(b);
    b[0] = 8;
    history.commitStroke(b);
    history.undo(b);

    // Slice 0's parked undo entry must still be there.
    history.stash(1);
    history.restore(0);
    expect(history.undoCount).toBe(1);
    const undone = history.undo(a);
    expect(undone?.kind).toBe("slice");
    if (undone?.kind === "slice") expect([...undone.raster]).toEqual([1]);
  });

  it("skips recordChange when before and after match", () => {
    const history = new SliceHistory();
    const ids = new Int32Array([4, 5]);
    expect(history.recordChange(ids, ids.slice())).toBe(false);
    expect(history.undoCount).toBe(0);
  });

  it("withChange only keeps history when mutate alters pixels", () => {
    const history = new SliceHistory();
    const ids = new Int32Array([1, 1, 1]);
    expect(history.withChange(ids, () => {})).toBe(false);
    expect(
      history.withChange(ids, () => {
        ids[2] = 0;
      }),
    ).toBe(true);
    expect(history.undoCount).toBe(1);
  });

  it("compound interpolate undo survives stash/restore and undoes as one step", () => {
    const history = new SliceHistory();
    const before1 = new Int32Array([0, 0]);
    const after1 = new Int32Array([9, 0]);
    const before2 = new Int32Array([0, 0]);
    const after2 = new Int32Array([0, 9]);
    expect(
      history.recordCompound([
        { index: 1, before: before1, after: after1 },
        { index: 2, before: before2, after: after2 },
      ]),
    ).toBe(true);
    expect(history.undoCount).toBe(1);

    // Navigate away — compound must remain undoable.
    history.stash(0);
    history.restore(5);
    expect(history.undoCount).toBe(1);

    const live = new Int32Array([1, 1]);
    const result = history.undo(live);
    expect(result?.kind).toBe("compound");
    if (result?.kind !== "compound") throw new Error("expected compound");
    expect(result.slices.map((s) => s.index)).toEqual([1, 2]);
    expect(history.undoCount).toBe(0);
    expect(history.redoCount).toBe(1);

    const redone = history.redo(live);
    expect(redone?.kind).toBe("compound");
    expect(history.undoCount).toBe(1);
  });

  it("undo prefers the more recent of slice vs compound", () => {
    const history = new SliceHistory();
    history.recordCompound([
      { index: 1, before: new Int32Array([0]), after: new Int32Array([1]) },
    ]);
    const ids = new Int32Array([0]);
    history.beginStroke(ids);
    ids[0] = 7;
    history.commitStroke(ids);

    // Brush is newer — undo brush first.
    const first = history.undo(ids);
    expect(first?.kind).toBe("slice");
    if (first?.kind !== "slice") throw new Error("expected slice");
    const second = history.undo(first.raster);
    expect(second?.kind).toBe("compound");
  });

  for (const tool of ["Interpolate", "Flood fill", "Split", "Merge", "Watershed", "Delete", "Track"]) {
    it(`${tool} plans remain pending and round-trip through compound undo/redo`, () => {
      const history = new SliceHistory();
      const pending = new PendingSliceBuffer();
      const before = new Int32Array([0, 2, 0]);
      const after = new Int32Array([7, 2, 7]);
      expect(history.recordCompound([{ index: 4, before, after }])).toBe(true);
      pending.markChanged(4, after, before);
      expect(pending.size).toBe(1); // dirty until explicit Save acknowledges it

      const undone = history.undo(after);
      expect(undone?.kind).toBe("compound");
      if (undone?.kind !== "compound") throw new Error("expected compound");
      expect([...undone.slices[0].before]).toEqual([...before]);
      const redone = history.redo(before);
      expect(redone?.kind).toBe("compound");
      if (redone?.kind !== "compound") throw new Error("expected compound");
      expect([...redone.slices[0].after]).toEqual([...after]);
      expect(pending.size).toBe(1);
    });
  }
});
