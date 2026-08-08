/** Which merge input the next canvas click writes (0 = first, 1 = second). */
export type MergeClickSlot = 0 | 1;

/**
 * One canvas click while Merge is active: write `label` into the current slot,
 * then advance to the other slot. Rejects background clicks and any label that
 * would duplicate the value already in the opposite input.
 */
export function applyMergeCanvasClick(
  label: number,
  mergeIdA: number | null,
  mergeIdB: number | null,
  slot: MergeClickSlot,
): {
  mergeIdA: number | null;
  mergeIdB: number | null;
  nextSlot: MergeClickSlot;
} | null {
  if (label <= 0) return null;
  const other = slot === 0 ? mergeIdB : mergeIdA;
  if (other != null && label === other) return null;
  if (slot === 0) {
    return { mergeIdA: label, mergeIdB, nextSlot: 1 };
  }
  return { mergeIdA, mergeIdB: label, nextSlot: 0 };
}

/** Which slot to target when entering Merge with the current input values. */
export function mergeClickSlotForInputs(
  mergeIdA: number | null,
  mergeIdB: number | null,
): MergeClickSlot {
  if (mergeIdB == null) return mergeIdA == null ? 0 : 1;
  return 0;
}
