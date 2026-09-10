/** Convert a CSS-pixel measurement to this canvas's image-coordinate units. */
export function screenPxToImagePx(screenPx: number, cssPixelsPerImagePixel: number): number {
  return screenPx / Math.max(cssPixelsPerImagePixel, 0.001);
}

/** Point prompts aim at one coordinate; their reticle is not a paint footprint. */
export const POINT_RETICLE_RADIUS_SCREEN_PX = 7;
export const POINT_RETICLE_ARM_SCREEN_PX = 11;

// Note there is deliberately no brush equivalent of the constants above. A
// point prompt marks one voxel, so its reticle is pure chrome and belongs in
// screen units; a brush ring stands for a real voxel footprint, so it is
// drawn from `brushRadius` and scaled into CSS pixels at the call site. A
// screen-constant brush ring understates the stroke by exactly the zoom
// factor, which is accurate on a 1:1 plane and wrong by 4x on a fitted 256px
// one — the ring must not be the one thing on screen that lies about what a
// click will change.

/** Size chrome to the display so small fitted planes retain CSS-pixel detail. */
export function cursorLayerBackingSize(
  cssWidth: number,
  cssHeight: number,
  devicePixelRatio: number,
): [number, number] {
  const dpr = devicePixelRatio > 0 ? devicePixelRatio : 1;
  return [
    Math.max(1, Math.round(cssWidth * dpr)),
    Math.max(1, Math.round(cssHeight * dpr)),
  ];
}

/**
 * CSS pixels per image pixel, per axis. Fit preserves the plane's aspect, so
 * these normally agree; they are returned separately so a stage that is ever
 * laid out non-uniformly cannot silently turn the reticle into an ellipse.
 */
export function imageToCssScale(
  cssWidth: number,
  cssHeight: number,
  imageWidth: number,
  imageHeight: number,
): [number, number] {
  return [
    imageWidth > 0 ? cssWidth / imageWidth : 1,
    imageHeight > 0 ? cssHeight / imageHeight : 1,
  ];
}

/** Keep label pixels native, then nearest-neighbour blit into display space. */
export function blitPlaneImageData(
  overlay: HTMLCanvasElement,
  plane: HTMLCanvasElement,
  image: ImageData,
  devicePixelRatio: number,
): { cssW: number; cssH: number; sx: number; sy: number } | null {
  const rect = overlay.getBoundingClientRect();
  const cssW = rect.width;
  const cssH = rect.height;
  if (cssW <= 0 || cssH <= 0) return null;
  const w = image.width;
  const h = image.height;
  if (w <= 0 || h <= 0) return null;

  if (plane.width !== w) plane.width = w;
  if (plane.height !== h) plane.height = h;
  const planeCtx = plane.getContext("2d");
  if (!planeCtx) return null;
  planeCtx.putImageData(image, 0, 0);

  const [backingW, backingH] = cursorLayerBackingSize(cssW, cssH, devicePixelRatio);
  if (overlay.width !== backingW) overlay.width = backingW;
  if (overlay.height !== backingH) overlay.height = backingH;
  const ctx = overlay.getContext("2d");
  if (!ctx) return null;
  ctx.setTransform(backingW / cssW, 0, 0, backingH / cssH, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, cssW, cssH);
  ctx.drawImage(plane, 0, 0, cssW, cssH);
  const [sx, sy] = imageToCssScale(cssW, cssH, w, h);
  return { cssW, cssH, sx, sy };
}

/** Fill runs with shared rounded edges so adjacent display rows have no gaps. */
export function fillMaskCssSpace(
  ctx: CanvasRenderingContext2D,
  mask: Uint8Array,
  h: number,
  w: number,
  sx: number,
  sy: number,
  rgb: readonly [number, number, number],
  alpha = 255,
): void {
  if (h <= 0 || w <= 0 || mask.length < h * w) return;
  ctx.save();
  ctx.fillStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha / 255})`;
  for (let y = 0; y < h; y++) {
    const row = y * w;
    const top = Math.round(y * sy);
    const bottom = Math.round((y + 1) * sy);
    const height = Math.max(1, bottom - top);
    let x = 0;
    while (x < w) {
      while (x < w && !mask[row + x]) x += 1;
      if (x >= w) break;
      const x0 = x;
      while (x < w && mask[row + x]) x += 1;
      const left = Math.round(x0 * sx);
      const right = Math.round(x * sx);
      ctx.fillRect(left, top, Math.max(1, right - left), height);
    }
  }
  ctx.restore();
}
