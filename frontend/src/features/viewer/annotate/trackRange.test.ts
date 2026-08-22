import { describe, expect, it } from "vitest";

import type { TrackingPrompt } from "../../../api/viewer";
import {
  canPropagatePrompt,
  promptSeedZs,
  toLayer,
  toZ,
  trackRangeIssue,
} from "./trackRange";

const prompt = (over: Partial<TrackingPrompt> = {}): TrackingPrompt => ({
  parent_id: 50,
  subclasses: [{ index: 1, seeds: [{ z: 4, shape: [2, 2], rle: [[0, 1]] }] }],
  start_z: 0,
  end_z: 9,
  z_range: [0, 9],
  status: "ready",
  ...over,
});

describe("Track layer numbering", () => {
  it("maps 0-based API z to the viewer's 1-based layer numbers", () => {
    // The viewer's z field reads `index + 1` out of `axisLen`; Start/End are
    // part of the same viewer and must not disagree with it by one.
    expect(toLayer(0)).toBe(1);
    expect(toLayer(9)).toBe(10);
    expect(toZ(1)).toBe(0);
    expect(toZ(10)).toBe(9);
    expect(toZ(toLayer(7))).toBe(7);
  });

  it("collects committed seed layers across every child, deduplicated", () => {
    expect(promptSeedZs(prompt({
      subclasses: [
        { index: 1, seeds: [{ z: 8, shape: [2, 2], rle: [[0, 1]] }, { z: 3, shape: [2, 2], rle: [[1, 1]] }] },
        { index: 2, seeds: [{ z: 3, shape: [2, 2], rle: [[2, 1]] }] },
      ],
    }))).toEqual([3, 8]);
  });
});

describe("trackRangeIssue", () => {
  it("accepts one seed inside a deliberately wider inclusive range", () => {
    expect(trackRangeIssue(prompt(), 20)).toBeNull();
    expect(canPropagatePrompt(prompt(), 20)).toBe(true);
  });

  it("accepts a single-layer range containing the seed", () => {
    expect(trackRangeIssue(prompt({ start_z: 4, end_z: 4 }), 20)).toBeNull();
  });

  it("requires both Start and End", () => {
    expect(trackRangeIssue(prompt({ start_z: null, end_z: null }), 20))
      .toMatch(/Set both Start and End/);
    expect(trackRangeIssue(prompt({ start_z: 2, end_z: null }), 20))
      .toMatch(/Set both Start and End/);
    expect(trackRangeIssue(prompt({ start_z: null, end_z: 8 }), 20))
      .toMatch(/Set both Start and End/);
  });

  it("rejects a reversed range and says so in layer numbers", () => {
    expect(trackRangeIssue(prompt({ start_z: 8, end_z: 2 }), 20))
      .toBe("End layer 3 must not be before Start layer 9.");
  });

  it("rejects a range past the end of the volume", () => {
    expect(trackRangeIssue(prompt({ start_z: 0, end_z: 25 }), 20))
      .toBe("End layer 26 is past the last layer (20).");
  });

  it("rejects a negative Start", () => {
    expect(trackRangeIssue(prompt({ start_z: -1, end_z: 5 }), 20))
      .toMatch(/before layer 1/);
  });

  it("skips the far-end check while the volume depth is unknown", () => {
    // `axisLength` answers before the volume metadata lands; the backend still
    // enforces the bound, so the field must not flap an error in the meantime.
    expect(trackRangeIssue(prompt({ start_z: 0, end_z: 500 }), 0)).toBeNull();
  });

  it("requires at least one non-empty prompt", () => {
    expect(trackRangeIssue(prompt({ subclasses: [{ index: 1, seeds: [] }] }), 20))
      .toMatch(/Draw at least one child-class seed/);
  });

  it("does not require prompts on either endpoint", () => {
    expect(trackRangeIssue(prompt({ start_z: 1, end_z: 9 }), 20)).toBeNull();
  });

  it("rejects a seed outside the chosen range, listing it 1-based", () => {
    expect(trackRangeIssue(prompt({ start_z: 6, end_z: 9 }), 20))
      .toBe("Seed layer 5 falls outside 7–10 (inclusive). Widen the range or clear those seeds.");
  });

  it("lists every offending seed layer", () => {
    const issue = trackRangeIssue(prompt({
      start_z: 4,
      end_z: 5,
      subclasses: [{
        index: 1,
        seeds: [
          { z: 1, shape: [2, 2], rle: [[0, 1]] },
          { z: 4, shape: [2, 2], rle: [[0, 1]] },
          { z: 9, shape: [2, 2], rle: [[0, 1]] },
        ],
      }],
    }), 20);
    expect(issue).toContain("Seed layers 2, 10");
  });

  it("asks for a selection when there is no prompt at all", () => {
    expect(trackRangeIssue(null, 20)).toMatch(/Select a queued parent/);
    expect(canPropagatePrompt(undefined, 20)).toBe(false);
  });
});
