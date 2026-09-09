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

/**
 * The cursor layer renders at *display* resolution, not image resolution.
 *
 * It used to share the label overlay's backing store — `canvas.width = w`,
 * the plane's own pixel width — and was then stretched to the stage with
 * `image-rendering: pixelated`. That is survivable while the chrome is
 * measured in voxels, but screen-constant chrome cannot survive it: a 7 CSS-px
 * reticle on a 256px plane fitted to ~1000px is 1.8 *image* pixels, drawn with
 * a 0.5px stroke into a 256-wide buffer and then magnified 4x by
 * nearest-neighbour. The same reticle on a 1024px plane is ~7 image pixels and
 * comes out clean — which is exactly why the cursor looked fine on some
 * volumes and like blocky noise on others.
 *
 * Sizing the buffer to the stage instead makes one CSS pixel one canvas unit,
 * so chrome is drawn at the resolution it is displayed at on every volume.
 */
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
