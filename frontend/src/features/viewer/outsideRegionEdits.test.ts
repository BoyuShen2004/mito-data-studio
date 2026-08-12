import { describe, expect, it } from "vitest";
import { OutsideRegionEditStore, OutsideRegionEdits } from "./outsideRegionEdits";

/**
 * The contract these tests hold to: Region only may hide things, but it may
 * never destroy pending work. Every case below toggles the mode (or the
 * policy) and asserts the raw paint is still recoverable.
 */

const plane = (values: number[]) => Int32Array.from(values);

const empty = { regionOnly: false, overwriteMode: "overwrite_empty" } as const;
const all = { regionOnly: false, overwriteMode: "overwrite_all" } as const;
const on = { regionOnly: true, overwriteMode: "overwrite_empty" } as const;

describe("OutsideRegionEdits", () => {
  it("shows outside paint exactly as drawn while Region only is on", () => {
    const edits = new OutsideRegionEdits();
    edits.record(2, 5); // painted over stored instance 5
    const painted = plane([0, 0, 9, 0]);

    // Region only on: the annotator is still working — show what they drew.
    expect([...edits.project(painted, on)]).toEqual([0, 0, 9, 0]);
  });

  it("keeps an outside edit that landed on empty space under either policy", () => {
    const edits = new OutsideRegionEdits();
    edits.record(1, 0); // stored label was background here
    const painted = plane([0, 7, 0, 0]);

    expect([...edits.project(painted, empty)]).toEqual([0, 7, 0, 0]);
    expect([...edits.project(painted, all)]).toEqual([0, 7, 0, 0]);
  });

  it("presents an edit over an existing label as that label under empty-only", () => {
    const edits = new OutsideRegionEdits();
    edits.record(1, 4); // painted over stored instance 4
    const painted = plane([0, 7, 0, 0]);

    expect([...edits.project(painted, empty)]).toEqual([0, 4, 0, 0]);
    expect([...edits.project(painted, all)]).toEqual([0, 7, 0, 0]);
  });

  it("treats an erase outside the region as an edit like any other", () => {
    const edits = new OutsideRegionEdits();
    edits.record(0, 6); // erased stored instance 6
    const painted = plane([0, 0, 0, 0]);

    // Empty-only: the erase did not land on empty space, so 6 stands.
    expect([...edits.project(painted, empty)]).toEqual([6, 0, 0, 0]);
    // Overwrite-all: the erase stands.
    expect([...edits.project(painted, all)]).toEqual([0, 0, 0, 0]);
  });

  it("never mutates the paint buffer, so toggling is fully reversible", () => {
    const edits = new OutsideRegionEdits();
    edits.record(1, 4);
    const painted = plane([0, 7, 0, 0]);

    edits.project(painted, empty);
    edits.project(painted, on);
    edits.project(painted, all);

    // The raw paint survived every projection.
    expect([...painted]).toEqual([0, 7, 0, 0]);
    // …and turning Region only back on still shows the original outside work.
    expect([...edits.project(painted, on)]).toEqual([0, 7, 0, 0]);
  });

  it("returns the same array when nothing would change", () => {
    const edits = new OutsideRegionEdits();
    const painted = plane([1, 2, 3]);
    // The overwhelmingly common case: Region only was never switched on, so
    // ordinary painting must not pay for this feature at all.
    expect(edits.project(painted, empty)).toBe(painted);

    edits.record(0, 0);
    expect(edits.project(painted, empty)).toBe(painted);
  });

  it("remembers the stored value, not the last painted one, on a repaint", () => {
    const edits = new OutsideRegionEdits();
    edits.record(0, 4);
    edits.record(0, 9); // painted again in the same session

    expect(edits.records()).toEqual([{ index: 0, baseline: 4 }]);
  });

  it("counts only pixels that still differ, so Undo silences the Save warning", () => {
    const edits = new OutsideRegionEdits();
    edits.record(0, 4);
    edits.record(1, 0);

    expect(edits.pendingCount(plane([7, 7, 0, 0]))).toBe(2);
    // Undone back to the stored values: nothing outside would be lost.
    expect(edits.pendingCount(plane([4, 0, 0, 0]))).toBe(0);
  });

  it("clones without aliasing, so freezing a plane detaches it", () => {
    const edits = new OutsideRegionEdits();
    edits.record(1, 2);
    const frozen = edits.clone();
    edits.record(4, 8);

    expect(frozen.size).toBe(1);
    expect(edits.size).toBe(2);
  });

  it("refreshes stored values when conflict recovery loads newer disk", () => {
    const edits = new OutsideRegionEdits();
    edits.record(1, 4);
    edits.rebase(plane([0, 9, 0]));

    expect(edits.records()).toEqual([{ index: 1, baseline: 9 }]);
  });
});

describe("OutsideRegionEditStore", () => {
  it("keeps one record per plane and counts the total for Save", () => {
    const store = new OutsideRegionEditStore();
    store.for(3).record(1, 0);
    store.for(3).record(2, 0);
    store.for(9).record(1, 0);

    expect(store.totalSize).toBe(3);
    expect(store.peek(3)?.size).toBe(2);
    expect(store.peek(4)).toBeUndefined();
  });

  it("drops a plane once its edits are acknowledged, and all of them on reset", () => {
    const store = new OutsideRegionEditStore();
    store.for(3).record(1, 0);
    store.for(9).record(1, 0);

    store.delete(3);
    expect(store.peek(3)).toBeUndefined();
    expect(store.totalSize).toBe(1);

    store.clear();
    expect(store.totalSize).toBe(0);
  });
});
