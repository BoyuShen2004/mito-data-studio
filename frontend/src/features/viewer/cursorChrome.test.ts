import { describe, expect, it, vi } from "vitest";
import { brushRadius } from "./brushCursor";
import {
  POINT_RETICLE_RADIUS_SCREEN_PX,
  blitPlaneImageData,
  cursorLayerBackingSize,
  fillMaskCssSpace,
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

describe("blitPlaneImageData", () => {
  it("sizes the overlay to the stage and draws nearest into CSS space", () => {
    const overlay = document.createElement("canvas");
    const plane = document.createElement("canvas");
    // jsdom getBoundingClientRect is 0×0 — stub a fitted 256² stage.
    vi.spyOn(overlay, "getBoundingClientRect").mockReturnValue({
      width: 800,
      height: 800,
      top: 0,
      left: 0,
      bottom: 800,
      right: 800,
      x: 0,
      y: 0,
      toJSON() {
        return {};
      },
    });
    const image = plane.getContext("2d")!.createImageData(256, 256);
    const result = blitPlaneImageData(overlay, plane, image, 1);
    expect(result).toEqual({ cssW: 800, cssH: 800, sx: 800 / 256, sy: 800 / 256 });
    expect(overlay.width).toBe(800);
    expect(overlay.height).toBe(800);
    expect(plane.width).toBe(256);
    expect(plane.height).toBe(256);
  });
});

describe("fillMaskCssSpace", () => {
  it("abuts neighbouring image pixels so fractional scales leave no row gaps", () => {
    // A solid 3×3 block on a 8×8 plane, drawn at the non-integer scale that
    // used to turn ImageData+pixelated upscale into horizontal hatching.
    const h = 8;
    const w = 8;
    const mask = new Uint8Array(h * w);
    for (let y = 2; y < 5; y++) for (let x = 2; x < 5; x++) mask[y * w + x] = 1;
    const sx = 800 / 256; // 3.125
    const sy = sx;
    const rects: Array<{ x: number; y: number; w: number; h: number }> = [];
    const ctx = {
      save: vi.fn(),
      restore: vi.fn(),
      fillRect: (x: number, y: number, rw: number, rh: number) => {
        rects.push({ x, y, w: rw, h: rh });
      },
      set fillStyle(_v: string) {},
    } as unknown as CanvasRenderingContext2D;
    fillMaskCssSpace(ctx, mask, h, w, sx, sy, [0, 255, 0], 255);
    expect(rects.length).toBe(3); // one run per occupied row
    // Shared edge between row y and y+1: bottom(y) === top(y+1).
    for (let i = 0; i < rects.length - 1; i++) {
      expect(rects[i].y + rects[i].h).toBe(rects[i + 1].y);
    }
    // Horizontal run covers [round(2*sx), round(5*sx)).
    expect(rects[0].x).toBe(Math.round(2 * sx));
    expect(rects[0].x + rects[0].w).toBe(Math.round(5 * sx));
  });
});
