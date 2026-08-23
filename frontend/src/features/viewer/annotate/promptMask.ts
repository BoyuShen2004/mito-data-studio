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
