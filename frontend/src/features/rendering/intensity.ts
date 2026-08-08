import type { ChunkTypedArray } from "../chunks/types";

export interface IntensityWindow {
  lo: number;
  hi: number;
}

export function validateWindow(window: IntensityWindow): IntensityWindow {
  if (!Number.isFinite(window.lo) || !Number.isFinite(window.hi) || window.hi <= window.lo) {
    throw new RangeError("intensity window must contain two finite increasing values");
  }
  return window;
}

/** Stable volume-wide mapping; NaN -> black, +Inf -> white, -Inf -> black. */
export function grayscaleRgba(
  values: ChunkTypedArray,
  window: IntensityWindow,
): Uint8ClampedArray {
  const { lo, hi } = validateWindow(window);
  const rgba = new Uint8ClampedArray(values.length * 4);
  for (let i = 0; i < values.length; i += 1) {
    const value = values[i];
    const gray = Number.isNaN(value)
      ? 0
      : value === Infinity
        ? 255
        : value === -Infinity
          ? 0
          : Math.max(0, Math.min(255, Math.round(((value - lo) * 255) / (hi - lo))));
    const at = i * 4;
    rgba[at] = gray;
    rgba[at + 1] = gray;
    rgba[at + 2] = gray;
    rgba[at + 3] = 255;
  }
  return rgba;
}

/**
 * The immutable ROI as one flat colour with a hard alpha edge — byte-for-byte
 * the layer `render_region_mask_slice_png` produces server-side, because the
 * editor uses this image twice: as the cyan overlay *and* as the CSS mask that
 * implements ROI-only. A soft or differently-coloured edge here would move the
 * ROI boundary depending on which transport served the plane.
 */
export const REGION_RGBA: readonly [number, number, number, number] = [14, 165, 233, 190];

export function regionRgba(values: ChunkTypedArray): Uint8ClampedArray {
  const rgba = new Uint8ClampedArray(values.length * 4);
  for (let i = 0; i < values.length; i += 1) {
    if (!values[i]) continue; // outside the ROI stays fully transparent
    const at = i * 4;
    rgba[at] = REGION_RGBA[0];
    rgba[at + 1] = REGION_RGBA[1];
    rgba[at + 2] = REGION_RGBA[2];
    rgba[at + 3] = REGION_RGBA[3];
  }
  return rgba;
}

/** Paint RGBA into a canvas, scaling with nearest-neighbour when a coarse mag
 * has to fill a full-resolution plane. Smoothing would invent voxel values that
 * are neither inside nor outside a mask. */
function canvasFromRgba(
  pixels: Uint8ClampedArray,
  shape: [number, number],
  targetShape: [number, number],
): HTMLCanvasElement {
  const [height, width] = shape;
  const source = document.createElement("canvas");
  source.width = width;
  source.height = height;
  const context = source.getContext("2d");
  if (!context) throw new Error("Canvas2D is unavailable");
  const imagePixels = new Uint8ClampedArray(pixels.length);
  imagePixels.set(pixels);
  context.putImageData(new ImageData(imagePixels, width, height), 0, 0);
  if (targetShape[0] === height && targetShape[1] === width) return source;
  const target = document.createElement("canvas");
  target.width = targetShape[1];
  target.height = targetShape[0];
  const targetContext = target.getContext("2d");
  if (!targetContext) throw new Error("Canvas2D is unavailable");
  targetContext.imageSmoothingEnabled = false;
  targetContext.drawImage(source, 0, 0, target.width, target.height);
  return target;
}

export function renderPlaneCanvas(
  values: ChunkTypedArray,
  shape: [number, number],
  window: IntensityWindow,
  targetShape = shape,
): HTMLCanvasElement {
  const [height, width] = shape;
  if (values.length !== height * width) throw new RangeError("plane shape does not match values");
  return canvasFromRgba(grayscaleRgba(values, window), shape, targetShape);
}

export function renderRegionCanvas(
  values: ChunkTypedArray,
  shape: [number, number],
  targetShape = shape,
): HTMLCanvasElement {
  const [height, width] = shape;
  if (values.length !== height * width) throw new RangeError("plane shape does not match values");
  return canvasFromRgba(regionRgba(values), shape, targetShape);
}

export function canvasObjectUrl(canvas: HTMLCanvasElement): Promise<string> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) reject(new Error("Canvas encoding failed"));
      else resolve(URL.createObjectURL(blob));
    }, "image/png");
  });
}
