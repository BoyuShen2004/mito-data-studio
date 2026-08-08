/**
 * Brush/erase footprint geometry and the cursor styles that draw it.
 *
 * ## Footprint
 *
 * The size control is the footprint's **width in pixels**, not its radius.
 * That is the whole fix behind "size 1 must paint one pixel": read as a
 * radius, `dx² + dy² <= 1` covers the centre *and* its four neighbours, so the
 * smallest brush the tool could offer was a five-pixel plus and single-voxel
 * corrections were impossible.
 *
 *   size 1 -> radius 0.5 -> only (0,0) satisfies dx²+dy² <= 0.25
 *   size 3 -> radius 1.5 -> the full 3x3 block
 *
 * `paintAt` and every cursor style take their geometry from here, so the ring
 * the annotator aims with and the pixels that change are the same disc.
 *
 * ## Hotspot
 *
 * Cursors are drawn in image-pixel space on a canvas the size of the plane, so
 * pixel (x, y) spans 0..1. Its *centre* is therefore (x + 0.5, y + 0.5), and
 * drawing at the integer coordinate — which is what the old cursor did — puts
 * the ring half a pixel up and to the left of the pixel that actually gets
 * painted. At high zoom on a size-1 brush that half-pixel is the difference
 * between hitting a membrane and missing it.
 */

export const BRUSH_MIN_SIZE = 1;
export const BRUSH_MAX_SIZE = 40;

/** Radius, in pixels, of a footprint `size` pixels across. */
export function brushRadius(size: number): number {
  return Math.max(size, BRUSH_MIN_SIZE) / 2;
}

export const CURSOR_STYLES = [
  { value: "disc", label: "Disc" },
  { value: "outline", label: "Outline" },
  { value: "crosshair", label: "Crosshair" },
  { value: "brackets", label: "Brackets" },
  { value: "dashed", label: "Dashed" },
] as const;

export type BrushCursorStyle = (typeof CURSOR_STYLES)[number]["value"];

export const DEFAULT_CURSOR_STYLE: BrushCursorStyle = "disc";

const STORAGE_KEY = "mito.brush-cursor-style";

export function isBrushCursorStyle(value: unknown): value is BrushCursorStyle {
  return CURSOR_STYLES.some((style) => style.value === value);
}

/** The saved preference, or the default. Never throws: a browser with storage
 * disabled just gets the default every session. */
export function loadBrushCursorStyle(): BrushCursorStyle {
  try {
    const stored = window.localStorage?.getItem(STORAGE_KEY);
    return isBrushCursorStyle(stored) ? stored : DEFAULT_CURSOR_STYLE;
  } catch {
    return DEFAULT_CURSOR_STYLE;
  }
}

export function saveBrushCursorStyle(style: BrushCursorStyle): void {
  try {
    window.localStorage?.setItem(STORAGE_KEY, style);
  } catch {
    /* see loadBrushCursorStyle */
  }
}
