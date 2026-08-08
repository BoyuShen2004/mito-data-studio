import { describe, expect, it } from "vitest";
import {
  applyMergeCanvasClick,
  mergeClickSlotForInputs,
} from "./mergeClick";

describe("applyMergeCanvasClick", () => {
  it("alternates first and second inputs on each accepted click", () => {
    let a: number | null = null;
    let b: number | null = null;
    let slot = mergeClickSlotForInputs(a, b);

    let r = applyMergeCanvasClick(5, a, b, slot);
    expect(r).toEqual({ mergeIdA: 5, mergeIdB: null, nextSlot: 1 });
    ({ mergeIdA: a, mergeIdB: b } = r!);
    slot = r!.nextSlot;

    r = applyMergeCanvasClick(7, a, b, slot);
    expect(r).toEqual({ mergeIdA: 5, mergeIdB: 7, nextSlot: 0 });
    ({ mergeIdA: a, mergeIdB: b } = r!);
    slot = r!.nextSlot;

    r = applyMergeCanvasClick(9, a, b, slot);
    expect(r).toEqual({ mergeIdA: 9, mergeIdB: 7, nextSlot: 1 });
    ({ mergeIdA: a, mergeIdB: b } = r!);
    slot = r!.nextSlot;

    r = applyMergeCanvasClick(12, a, b, slot);
    expect(r).toEqual({ mergeIdA: 9, mergeIdB: 12, nextSlot: 0 });
  });

  it("rejects a label that would duplicate the opposite input", () => {
    let a = 5;
    let b = 7;
    let slot: 0 | 1 = 0;

    expect(applyMergeCanvasClick(7, a, b, slot)).toBeNull();
    expect(applyMergeCanvasClick(5, a, b, 1)).toBeNull();

    const r = applyMergeCanvasClick(9, a, b, slot);
    expect(r).toEqual({ mergeIdA: 9, mergeIdB: 7, nextSlot: 1 });
    a = r!.mergeIdA!;
    slot = r!.nextSlot;

    expect(applyMergeCanvasClick(9, a, b, slot)).toBeNull();
    expect(applyMergeCanvasClick(12, a, b, slot)).toEqual({
      mergeIdA: 9,
      mergeIdB: 12,
      nextSlot: 0,
    });
  });

  it("rejects background and keeps slot unchanged on duplicate attempts", () => {
    expect(applyMergeCanvasClick(0, null, null, 0)).toBeNull();
    expect(applyMergeCanvasClick(5, 5, null, 1)).toBeNull();
  });
});

describe("mergeClickSlotForInputs", () => {
  it("targets the first empty input, otherwise the first box for replacement", () => {
    expect(mergeClickSlotForInputs(null, null)).toBe(0);
    expect(mergeClickSlotForInputs(5, null)).toBe(1);
    expect(mergeClickSlotForInputs(5, 7)).toBe(0);
  });
});
