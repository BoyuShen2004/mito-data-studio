/**
 * "Region only" display semantics: a mitochondrion that touches the region
 * *anywhere in the volume* is shown whole, on every plane; one that never
 * touches it is hidden. That is a per-instance decision, so it cannot be
 * expressed as the CSS `mask-image` clip this replaced — a mask clips pixels,
 * and clipping cut every instance off at the ROI boundary.
 *
 * **Membership has two halves, and needs both.** The volume-wide half is the
 * server's (`/tasks/<id>/region-label-ids/`): only it can see the planes the
 * browser has never loaded, and without it a mito that enters the ROI on five
 * of its forty planes vanished on the other thirty-five. The per-plane half is
 * computed here, from the label ids the canvas already holds, because the
 * annotator's *unsaved* paint is in that buffer and not on the server — a
 * server-only set would hide a brand-new instance the moment it was drawn
 * inside the ROI. `regionMembership` unions them.
 *
 * Read-only. Nothing here touches the label buffer or any write path.
 */

/** Decode a region-mask PNG into a 0/1 mask sampled at the label's `w` x `h`.
 *
 * The ROI overlay can arrive at a coarser magnification than the label plane
 * (the chunk renderer serves whatever mag is ready), so it is drawn *scaled*
 * into a `w` x `h` canvas — exactly what `mask-size: 100% 100%` did for the
 * CSS mask, keeping the two paths visually identical. */
export async function decodeRegionMask(
  url: string,
  w: number,
  h: number,
): Promise<Uint8Array> {
  if (w <= 0 || h <= 0) return new Uint8Array(0);
  const image = await loadImage(url);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new Error("2D canvas unavailable");
  ctx.clearRect(0, 0, w, h);
  // Nearest-neighbour, matching how a coarse ROI mag is painted everywhere
  // else: smoothing would invent partial alpha along the boundary, i.e. voxels
  // that are neither inside the region nor outside it.
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(image, 0, 0, w, h);
  const { data } = ctx.getImageData(0, 0, w, h);
  const mask = new Uint8Array(w * h);
  // The region overlay is a single flat colour with a constant alpha inside
  // the ROI and zero outside it, so alpha alone answers "inside?".
  for (let i = 0; i < mask.length; i++) mask[i] = data[i * 4 + 3] > 0 ? 1 : 0;
  return mask;
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("Could not decode the region overlay"));
    image.src = url;
  });
}

/** The instance ids with at least one pixel inside the region on this plane.
 *
 * Walks runs rather than pixels: a label plane is overwhelmingly long runs of
 * one id, and this is on the repaint path of every brush stroke. */
export function labelIdsTouchingRegion(
  ids: Int32Array,
  region: Uint8Array,
): Set<number> {
  const touching = new Set<number>();
  if (region.length !== ids.length) return touching;
  let previous = 0;
  for (let i = 0; i < ids.length; i++) {
    if (region[i] === 0) continue;
    const id = ids[i];
    if (id <= 0 || id === previous) continue;
    previous = id;
    touching.add(id);
  }
  return touching;
}

/** Union the volume-wide membership set with what this plane shows.
 *
 * Returns a *new, mutable* set the caller may keep adding to as further planes
 * are decoded, or `null` when neither half is known — which callers read as
 * "not filtering yet", not as "nothing is in the region".
 */
export function regionMembership(
  volumeIds: ReadonlySet<number> | null | undefined,
  planeIds: ReadonlySet<number> | null | undefined,
): Set<number> | null {
  if (!volumeIds && !planeIds) return null;
  const merged = new Set<number>(volumeIds ?? []);
  if (planeIds) for (const id of planeIds) merged.add(id);
  return merged;
}

/** Restore edits that would alter an instance hidden by Region only.
 *
 * Visibility defaults to the labels that touch the ROI on this plane, but the
 * caller passes `visibleIds` so the guard uses the *same* volume-wide
 * membership the overlay draws with: an instance the annotator can see (because
 * it reaches the ROI on some other plane) must also be editable here, or Region
 * only would silently revert strokes on labels it is showing.
 *
 * Background is always editable; every other existing label is protected.
 * Keeping this as a single post-plan rule lets bulk tools share the exact
 * brush/erase contract. Returns the number of pixels restored in `after`.
 */
export function protectHiddenRegionLabels(
  before: Int32Array,
  after: Int32Array,
  region: Uint8Array,
  visibleIds?: ReadonlySet<number> | null,
): number {
  if (before.length !== after.length || region.length !== before.length) return 0;
  const visible = visibleIds ?? labelIdsTouchingRegion(before, region);
  let restored = 0;
  for (let i = 0; i < before.length; i++) {
    const prior = before[i];
    if (prior <= 0 || visible.has(prior) || after[i] === prior) continue;
    after[i] = prior;
    restored += 1;
  }
  return restored;
}
