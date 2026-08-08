import { describe, expect, it } from "vitest";
import { displayLayer, displayLayerRange, displayTaskLayerRange, parseLayerInput } from "./layerIndex";

describe("layerIndex", () => {
  it("maps 0-based storage to 1-based display", () => {
    expect(displayLayer(0)).toBe(1);
    expect(displayLayer(9)).toBe(10);
    expect(displayLayerRange(0, 0)).toBe("1");
    expect(displayLayerRange(0, 9)).toBe("1–10");
  });

  it("formats task bounds as half-open spans", () => {
    expect(displayTaskLayerRange(0, 256)).toBe("1–256");
    expect(displayTaskLayerRange(3, 8)).toBe("4–8");
    expect(displayTaskLayerRange(0, 1)).toBe("1");
  });

  it("parses 1-based inputs back to 0-based indices", () => {
    expect(parseLayerInput("")).toBeNull();
    expect(parseLayerInput("0")).toBeNull();
    expect(parseLayerInput("1")).toBe(0);
    expect(parseLayerInput("10")).toBe(9);
  });
});
