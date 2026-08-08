import { describe, expect, it } from "vitest";
import {
  rememberLabelLayer,
  rememberedNonAdjacentPair,
  type InterpolateLayerMemory,
} from "./interpolateLayerMemory";

describe("interpolate layer memory", () => {
  it("returns the last two usable layers ordered low to high", () => {
    const memory: InterpolateLayerMemory = new Map();
    rememberLabelLayer(memory, "z", 7, 12);
    rememberLabelLayer(memory, "z", 7, 4);
    expect(rememberedNonAdjacentPair(memory, "z", 7)).toEqual([4, 12]);
  });

  it("skips a newest adjacent touch without forgetting an older usable layer", () => {
    const memory: InterpolateLayerMemory = new Map();
    rememberLabelLayer(memory, "z", 7, 3);
    rememberLabelLayer(memory, "z", 7, 8);
    rememberLabelLayer(memory, "z", 7, 7);
    expect(rememberedNonAdjacentPair(memory, "z", 7)).toEqual([3, 7]);
  });

  it("keeps axes and labels independent and moves a repeated layer to newest", () => {
    const memory: InterpolateLayerMemory = new Map();
    rememberLabelLayer(memory, "z", 2, 1);
    rememberLabelLayer(memory, "z", 2, 6);
    rememberLabelLayer(memory, "z", 2, 1);
    rememberLabelLayer(memory, "y", 2, 20);
    rememberLabelLayer(memory, "z", 3, 30);
    expect(rememberedNonAdjacentPair(memory, "z", 2)).toEqual([1, 6]);
    expect(rememberedNonAdjacentPair(memory, "y", 2)).toBeNull();
    expect(rememberedNonAdjacentPair(memory, "z", 3)).toBeNull();
  });
});
