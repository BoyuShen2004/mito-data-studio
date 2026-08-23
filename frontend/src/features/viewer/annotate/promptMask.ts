/**
 * Accumulating tracking seeds on a layer.
 *
 * A SAM2 Box/Point prediction describes the object of that one gesture and
 * nothing else — it is blind to whatever the annotator already seeded on the
 * same layer. Writing a committed proposal back as *the* layer mask therefore
 * erased every earlier prompt, so a layer could only ever keep the last object
 * drawn, however many the annotator meant to mark.
 *
 * Seeds accumulate instead. Separated blobs are not a mess to be resolved here:
 * they are precisely how the annotator asks for several tracked objects, and
 * the backend splits the layer into branches by 8-connected component
 * (`annotation.tracking.components.split_components`). Keeping them merged in
 * one mask is what lets that inference see them at all.
 */

/**
 * `base ∪ addition`, without mutating either.
 *
 * A `base` of a different length is a layer whose shape changed underneath the
 * prediction; there is nothing meaningful to union, so the fresh proposal wins
 * rather than being blended with pixels that no longer line up.
 */
export function mergePromptMask(
  base: Uint8Array | null | undefined,
  addition: Uint8Array,
): Uint8Array {
  const merged = addition.slice();
  if (!base || base.length !== merged.length) return merged;
  for (let i = 0; i < merged.length; i += 1) if (base[i]) merged[i] = 1;
  return merged;
}

/** True-run RLE (`[start, length]` of contiguous truthy pixels) — the shape the
 *  tracking endpoint expects for seed masks, distinct from the label-id RLE. */
export function trueRunsRLE(mask: Uint8Array): [number, number][] {
  const runs: [number, number][] = [];
  let i = 0;
  while (i < mask.length) {
    if (mask[i]) {
      const start = i;
      while (i < mask.length && mask[i]) i++;
      runs.push([start, i - start]);
    } else {
      i++;
    }
  }
  return runs;
}

export function maskFromTrackingSeed(runs: [number, number][], size: number): Uint8Array {
  const mask = new Uint8Array(size);
  for (const [start, length] of runs) mask.fill(1, start, Math.min(size, start + length));
  return mask;
}

interface SeedSlot {
  index: number;
  seeds: { z: number; rle: [number, number][]; shape: [number, number] }[];
}

interface SeedOverlay {
  parentId: number;
  mask: Uint8Array;
  /** 2 = the class being edited, 0 = the rest of the queue. */
  emphasis: 0 | 2;
}

/**
 * Which seed masks the tracking overlay paints for one layer.
 *
 * The selected class is drawn **once, from the live editing surface** — which
 * already holds the union of whatever slots it has, including none at all.
 * Deciding that from a "currently selected slot" index instead is what made a
 * class's seeds invisible whenever the index was stale or the class had no slot
 * yet: every slot got skipped as "not the selected one", the live surface was
 * never drawn, and the annotator painted onto a canvas that showed nothing.
 *
 * Every other queued class is drawn from its durable seeds, per slot.
 */
export function trackingSeedOverlays({
  prompts, selectedParentId, selectedMask, z, height, width,
}: {
  prompts: { parent_id: number; subclasses: SeedSlot[] }[];
  selectedParentId: number | null;
  selectedMask: Uint8Array | null;
  z: number;
  height: number;
  width: number;
}): SeedOverlay[] {
  const overlays: SeedOverlay[] = [];
  for (const prompt of prompts) {
    if (prompt.parent_id === selectedParentId) {
      if (selectedMask?.some(Boolean)) {
        overlays.push({ parentId: prompt.parent_id, mask: selectedMask, emphasis: 2 });
      }
      continue;
    }
    for (const slot of prompt.subclasses) {
      const seed = slot.seeds.find((item) => item.z === z);
      if (!seed || seed.shape[0] !== height || seed.shape[1] !== width) continue;
      const mask = maskFromTrackingSeed(seed.rle, height * width);
      if (!mask.some(Boolean)) continue;
      overlays.push({ parentId: prompt.parent_id, mask, emphasis: 0 });
    }
  }
  // The edited class paints last so it stays legible over the rest.
  return overlays.sort((a, b) => a.emphasis - b.emphasis);
}
