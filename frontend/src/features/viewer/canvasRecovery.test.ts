import { describe, expect, it } from "vitest";
import { stageIntersectsViewport } from "./canvasRecovery";

describe("canvas black-padding recovery", () => {
  it("detects a stage stranded outside the viewport", () => {
    expect(stageIntersectsViewport(
      { scrollLeft: 900, scrollTop: 900, clientWidth: 200, clientHeight: 200 },
      { stageLeft: 100, stageTop: 100, stageW: 400, stageH: 400 },
    )).toBe(false);
  });

  it("accepts partial intersection but rejects zero-size stages", () => {
    const viewport = { scrollLeft: 450, scrollTop: 450, clientWidth: 200, clientHeight: 200 };
    expect(stageIntersectsViewport(
      viewport,
      { stageLeft: 100, stageTop: 100, stageW: 400, stageH: 400 },
    )).toBe(true);
    expect(stageIntersectsViewport(
      viewport,
      { stageLeft: 100, stageTop: 100, stageW: 0, stageH: 400 },
    )).toBe(false);
  });
});
