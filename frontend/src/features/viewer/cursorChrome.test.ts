import { describe, expect, it } from "vitest";
import { brushRadius } from "./brushCursor";
import {
  POINT_RETICLE_RADIUS_SCREEN_PX,
  cursorLayerBackingSize,
  imageToCssScale,
  screenPxToImagePx,
} from "./cursorChrome";

describe("screen-space cursor chrome", () => {
  it("keeps the Point Mask reticle the same CSS size on small and large fitted planes", () => {
    const renderedWidth = 800;
    const smallScale = renderedWidth / 256;
    const largeScale = renderedWidth / 1024;
    const smallImageRadius = screenPxToImagePx(POINT_RETICLE_RADIUS_SCREEN_PX, smallScale);
    const largeImageRadius = screenPxToImagePx(POINT_RETICLE_RADIUS_SCREEN_PX, largeScale);

    expect(smallImageRadius * smallScale).toBeCloseTo(POINT_RETICLE_RADIUS_SCREEN_PX);
    expect(largeImageRadius * largeScale).toBeCloseTo(POINT_RETICLE_RADIUS_SCREEN_PX);
  });

  it("draws the brush ring as the voxel footprint it will actually paint", () => {
    // The ring is not chrome: on every plane it must cover exactly the disc
    // `paintAt` changes. A screen-constant ring understated the stroke by the
    // zoom factor — right at 1:1, wrong by 4x on a fitted 256px plane.
    for (const [plane, stage] of [[256, 800], [1024, 800], [64, 512]] as const) {
      const [sx] = imageToCssScale(stage, stage, plane, plane);
      const paintedVoxelRadius = brushRadius(12);
      const ringCssRadius = paintedVoxelRadius * sx;
      expect(ringCssRadius / sx).toBeCloseTo(paintedVoxelRadius);
      expect(ringCssRadius).toBeCloseTo(6 * (stage / plane));
    }
  });

  it("keeps small brush sizes visually distinguishable", () => {
    const [sx] = imageToCssScale(800, 800, 256, 256); // scale 3.125
    expect(brushRadius(1) * sx).toBeCloseTo(1.5625);
    expect(brushRadius(6) * sx).toBeCloseTo(9.375);
    // A screen-constant ring would have squashed these into 0.5px and 3px.
    expect(brushRadius(6) * sx - brushRadius(1) * sx).toBeGreaterThan(7);
  });
});

describe("cursor layer renders at display resolution", () => {
  it("sizes its buffer from the stage, not from the plane", () => {
    // The stage is the same on both volumes; only the plane differs.
    expect(cursorLayerBackingSize(800, 800, 1)).toEqual([800, 800]);
    expect(cursorLayerBackingSize(800, 800, 2)).toEqual([1600, 1600]);
  });

  it("never collapses to a zero-sized buffer", () => {
    expect(cursorLayerBackingSize(0.2, 0.2, 1)).toEqual([1, 1]);
    expect(cursorLayerBackingSize(800, 800, 0)).toEqual([800, 800]);
  });

  it("draws the reticle at the same on-screen size and resolution on any plane", () => {
    // Before the fix the reticle was drawn in image units, so its rendered
    // detail collapsed as the plane got smaller: 7 CSS px is only 1.8 image
    // pixels of buffer on a fitted 256px plane, versus ~7 on a 1024px one.
    const small = screenPxToImagePx(POINT_RETICLE_RADIUS_SCREEN_PX, 800 / 256);
    const large = screenPxToImagePx(POINT_RETICLE_RADIUS_SCREEN_PX, 800 / 1024);
    expect(small).toBeCloseTo(2.24);
    expect(large).toBeCloseTo(8.96);
    // Drawing in CSS pixels instead, the radius is the constant itself, and
    // one unit is one screen pixel whatever the plane measures.
    for (const plane of [64, 256, 1024, 4096]) {
      const [sx, sy] = imageToCssScale(800, 800, plane, plane);
      expect(sx).toBeCloseTo(800 / plane);
      expect(sy).toBeCloseTo(sx);
      expect(cursorLayerBackingSize(800, 800, 1)).toEqual([800, 800]);
    }
  });

  it("maps a hovered image pixel onto its stage position per axis", () => {
    const [sx, sy] = imageToCssScale(1000, 250, 256, 64);
    expect(sx).toBeCloseTo(1000 / 256);
    expect(sy).toBeCloseTo(250 / 64);
    // A non-square plane keeps a round reticle round because position, not
    // radius, is what the per-axis scale is applied to.
    expect(128 * sx).toBeCloseTo(500);
    expect(32 * sy).toBeCloseTo(125);
  });

  it("falls back to 1:1 rather than dividing by a zero-sized plane", () => {
    expect(imageToCssScale(800, 800, 0, 0)).toEqual([1, 1]);
  });
});
