import { describe, expect, it } from "vitest";
import { applyInterpolateCanvasClick } from "./interpolateClick";

describe("applyInterpolateCanvasClick", () => {
  it("follows the clicked label and anchors the first layer", () => {
    const r = applyInterpolateCanvasClick(9, 4, null, null, null);
    expect(r).toEqual({
      activeId: 9,
      anchor: { label: 9, layer: 4 },
      interpFirst: null,
      interpLast: null,
    });
  });

  it("sets start/end from two same-label clicks on different layers", () => {
    let anchor = applyInterpolateCanvasClick(9, 10, null, null, null)!.anchor;
    const r = applyInterpolateCanvasClick(9, 4, anchor, null, null);
    expect(r).toEqual({
      activeId: 9,
      anchor: null,
      interpFirst: 4,
      interpLast: 10,
    });
  });

  it("orders endpoints by layer index regardless of click order", () => {
    let anchor = applyInterpolateCanvasClick(9, 12, null, null, null)!.anchor;
    const r = applyInterpolateCanvasClick(9, 3, anchor, null, null);
    expect(r?.interpFirst).toBe(3);
    expect(r?.interpLast).toBe(12);
  });

  it("resets endpoints when a different label is clicked", () => {
    const r = applyInterpolateCanvasClick(
      7,
      8,
      { label: 9, layer: 4 },
      2,
      9,
    );
    expect(r).toEqual({
      activeId: 7,
      anchor: { label: 7, layer: 8 },
      interpFirst: null,
      interpLast: null,
    });
  });

  it("ignores background clicks", () => {
    expect(applyInterpolateCanvasClick(0, 5, null, null, null)).toBeNull();
  });
});
