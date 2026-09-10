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

/**
 * Fill a mask's runs on the display-resolution layer as one path.
 *
 * Run edges are rounded to CSS pixels so neighbouring rows share an edge
 * exactly. They go into a single path filled once, not one `fillRect` each:
 * separate fills antialias every shared edge twice, which a translucent fill
 * at a fractional devicePixelRatio shows as faint dark seams between rows.
 */
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
  ctx.beginPath();
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
      ctx.rect(left, top, Math.max(1, right - left), height);
    }
  }
  ctx.fill();
  ctx.restore();
}

/**
 * Outline segments for a mask, in grid coordinates: `[x0, y0, x1, y1, ...]`.
 *
 * Cached per mask object. A proposal mask is a fresh array for every
 * prediction and is never written in place, while the cursor layer repaints on
 * every pointer move; tracing once per mask keeps a move over a 2048² plane
 * from re-scanning four million pixels to redraw the same outline. Kept in
 * grid units so a zoom or resize reuses it too.
 */
const contourCache = new WeakMap<Uint8Array, { h: number; w: number; segments: Int32Array }>();

function maskContourSegments(mask: Uint8Array, h: number, w: number): Int32Array {
  const cached = contourCache.get(mask);
  if (cached && cached.h === h && cached.w === w) return cached.segments;
  // Each row's leftmost and rightmost set pixel (lo > hi for an empty row).
  // Edges can only occur within the union of a row's extent and the one above
  // it, so a proposal covering a few percent of a large plane is traced over a
  // few percent of it.
  const lo = new Int32Array(h).fill(w);
  const hi = new Int32Array(h).fill(-1);
  for (let y = 0; y < h; y++) {
    const row = y * w;
    let x = 0;
    while (x < w && mask[row + x] === 0) x += 1;
    if (x === w) continue;
    lo[y] = x;
    let r = w - 1;
    while (mask[row + r] === 0) r -= 1;
    hi[y] = r;
  }
  const out: number[] = [];
  // `openAt[x]` is the grid row where column x's current vertical run began.
  // A run open after row y-1 lies within that row's extent, which is always
  // part of row y's scan, so every run is closed where it ends.
  const openAt = new Int32Array(w + 1).fill(-1);
  for (let y = 0; y <= h; y++) {
    const from = Math.min(y < h ? lo[y] : w, y > 0 ? lo[y - 1] : w);
    const to = Math.max(y < h ? hi[y] : -1, y > 0 ? hi[y - 1] : -1);
    if (from > to) continue;
    const above = (y - 1) * w;
    const below = y * w;
    const hasAbove = y > 0;
    const hasBelow = y < h;
    // Horizontal edges on grid row y, between image rows y-1 and y; collinear
    // unit edges merge into one segment.
    let x = from;
    while (x <= to) {
      if ((hasAbove && mask[above + x] !== 0) === (hasBelow && mask[below + x] !== 0)) {
        x += 1;
        continue;
      }
      const x0 = x;
      do {
        x += 1;
      } while (
        x <= to
        && (hasAbove && mask[above + x] !== 0) !== (hasBelow && mask[below + x] !== 0)
      );
      out.push(x0, y, x, y);
    }
    // Vertical edges on grid columns from..to+1 for image row y.
    for (let c = from; c <= to + 1; c++) {
      const edge =
        hasBelow && (c > 0 && mask[below + c - 1] !== 0) !== (c < w && mask[below + c] !== 0);
      if (edge) {
        if (openAt[c] < 0) openAt[c] = y;
      } else if (openAt[c] >= 0) {
        out.push(c, openAt[c], c, y);
        openAt[c] = -1;
      }
    }
  }
  const segments = Int32Array.from(out);
  contourCache.set(mask, { h, w, segments });
  return segments;
}

/**
 * Outline a mask along its pixel-grid boundary on the display-resolution layer.
 *
 * Grid lines map to CSS pixels with the same rounding `fillMaskCssSpace` uses,
 * so the outline sits exactly on the fill's edge at every zoom. Square caps
 * close the corners where a horizontal segment meets a vertical one.
 */
export function strokeMaskContourCssSpace(
  ctx: CanvasRenderingContext2D,
  mask: Uint8Array,
  h: number,
  w: number,
  sx: number,
  sy: number,
  lineWidth: number,
  color: string,
): void {
  if (h <= 0 || w <= 0 || mask.length < h * w) return;
  const segments = maskContourSegments(mask, h, w);
  if (segments.length === 0) return;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.lineCap = "square";
  ctx.beginPath();
  for (let i = 0; i < segments.length; i += 4) {
    ctx.moveTo(Math.round(segments[i] * sx), Math.round(segments[i + 1] * sy));
    ctx.lineTo(Math.round(segments[i + 2] * sx), Math.round(segments[i + 3] * sy));
  }
  ctx.stroke();
  ctx.restore();
}
