const SMALL_PAN_FRACTION = 0.2;
const LARGE_PAN_FRACTION = 0.75;
const MIN_SMALL_PAN_PX = 64;
const MIN_LARGE_PAN_PX = 256;

/** Pan the viewport horizontally without changing the displayed slice or zoom. */
export function panCanvasHorizontally(
  viewport: HTMLElement | null,
  direction: -1 | 1,
  large = false,
) {
  if (!viewport) return;
  const step = large
    ? Math.max(MIN_LARGE_PAN_PX, viewport.clientWidth * LARGE_PAN_FRACTION)
    : Math.max(MIN_SMALL_PAN_PX, viewport.clientWidth * SMALL_PAN_FRACTION);
  viewport.scrollLeft += direction * step;
}

/** Pan the viewport vertically without changing the displayed slice or zoom. */
export function panCanvasVertically(
  viewport: HTMLElement | null,
  direction: -1 | 1,
  large = false,
) {
  if (!viewport) return;
  const step = large
    ? Math.max(MIN_LARGE_PAN_PX, viewport.clientHeight * LARGE_PAN_FRACTION)
    : Math.max(MIN_SMALL_PAN_PX, viewport.clientHeight * SMALL_PAN_FRACTION);
  viewport.scrollTop += direction * step;
}
