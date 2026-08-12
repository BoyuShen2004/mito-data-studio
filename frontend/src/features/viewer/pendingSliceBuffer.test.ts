import { describe, expect, it } from "vitest";
import { PendingSliceBuffer } from "./pendingSliceBuffer";

describe("PendingSliceBuffer", () => {
  it("retains only slices whose pixels actually changed", () => {
    const pending = new PendingSliceBuffer();
    const edited = new Int32Array([0, 7, 0]);

    pending.markChanged(3, edited, new Int32Array([0, 0, 0]));
    edited[1] = 99;
    expect([...pending.get(3)!]).toEqual([0, 7, 0]);

    expect(pending.freeze(3, edited)).toBe(true);

    // Navigating across clean slices must not turn them into dirty entries.
    for (let index = 4; index < 100; index += 1) {
      expect(pending.freeze(index, new Int32Array([0, 0, 0]))).toBe(false);
    }

    expect(pending.size).toBe(1);
    expect([...pending.get(3)!]).toEqual([0, 99, 0]);
  });

  it("freezes a dirty slice before its live canvas buffer is reused", () => {
    const pending = new PendingSliceBuffer();
    const live = new Int32Array([1, 2, 3]);
    pending.markChanged(8, live, new Int32Array([0, 0, 0]));
    pending.freeze(8, live);

    live.fill(9);

    expect([...pending.get(8)!]).toEqual([1, 2, 3]);
  });

  it("does not acknowledge a newer edit with an older save response", () => {
    const pending = new PendingSliceBuffer();
    pending.markChanged(5, new Int32Array([1]), new Int32Array([0]));
    const staleSave = pending.snapshots()[0];

    pending.markChanged(5, new Int32Array([2]), new Int32Array([1]));

    expect(pending.acknowledge(5, staleSave.revision)).toBe(false);
    expect(pending.size).toBe(1);
    expect([...pending.get(5)!]).toEqual([2]);
  });

  it("acknowledges exactly the revision that reached the server", () => {
    const pending = new PendingSliceBuffer();
    pending.markChanged(2, new Int32Array([4]), new Int32Array([0]));
    const saved = pending.snapshots()[0];

    expect(pending.acknowledge(2, saved.revision)).toBe(true);
    expect(pending.size).toBe(0);
  });

  it("returns immutable save snapshots", () => {
    const pending = new PendingSliceBuffer();
    const live = new Int32Array([3, 4]);
    pending.markChanged(1, live, new Int32Array([0, 0]));
    const snapshot = pending.snapshots()[0];

    live[0] = 99;

    expect([...snapshot.ids]).toEqual([3, 4]);
  });

  it("rebases non-overlapping edits and keeps newer server pixels on overlap", () => {
    const pending = new PendingSliceBuffer();
    pending.markChanged(
      4,
      new Int32Array([7, 0, 8, 0]),
      new Int32Array([0, 0, 0, 0]),
    );

    const result = pending.rebase(4, new Int32Array([0, 6, 9, 0]));

    expect(result).toEqual({ reapplied: 1, conflicts: 1, pending: true });
    expect([...pending.get(4)!]).toEqual([7, 6, 9, 0]);
  });

  it("drops a pending plane when every local change overlaps newer work", () => {
    const pending = new PendingSliceBuffer();
    pending.markChanged(2, new Int32Array([5]), new Int32Array([0]));

    expect(pending.rebase(2, new Int32Array([9]))).toEqual({
      reapplied: 0,
      conflicts: 1,
      pending: false,
    });
    expect(pending.size).toBe(0);
  });
});
