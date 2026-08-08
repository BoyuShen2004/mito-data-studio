/**
 * Which instance id **New** should hand out.
 *
 * The rule is "the smallest positive integer nothing is using yet", counting
 * every id that exists in the volume's working copy (the Labels summary), every
 * id sitting in an unsaved plane or the live paint buffer, and every id
 * reserved as a Track parent class.
 *
 * This replaced `max + 1`, which skipped every hole a Merge, a Delete, a Reject
 * or an abandoned Track parent left behind: a volume that had once reached id
 * 4000 kept minting 4001, 4002, … even with thousands of ids free below.
 *
 * Two properties the old form did not have, and the reason this is a set
 * rather than a maximum:
 *
 * - **Idempotent.** The current Active id is *not* treated as used, so clicking
 *   New twice without painting anything returns the same free id both times
 *   instead of walking upwards. Painting into it (or queueing it as a Track
 *   parent) is what makes the next click move on.
 * - **Hole-filling.** Ids 1–101 and 103–115 used ⇒ New gives 102; once 102 is
 *   occupied ⇒ New gives 116.
 */

/** Ceiling on the bitset, i.e. on how far a scan will look. 4M ids is far past
 * any instance count these volumes reach, and caps the allocation at 4MB. Above
 * it the id space is treated as dense, which is the same answer `max + 1` gave.
 */
export const MAX_TRACKED_LABEL_ID = 1 << 22;

/**
 * How large a bitset has to be for the answer to be inside it.
 *
 * With `n` values fed in there are at most `n` distinct ids, so the smallest
 * free id is at most `n + 1` — whatever the ids themselves are. Sizing from the
 * *counts* (which every source knows without being read) is what keeps this
 * O(1) rather than a counting pass over every pending plane.
 */
export function usedLabelIdCapacity(...counts: number[]): number {
  let total = 1;
  for (const count of counts) total += Math.max(0, count);
  return Math.min(total, MAX_TRACKED_LABEL_ID);
}

/** A bitset of "this instance id is taken", scanned from 1 upwards. */
export class UsedLabelIds {
  private readonly taken: Uint8Array;

  constructor(capacity: number) {
    // +2: index 0 is unused (ids are 1-based) and the answer may be capacity+1.
    this.taken = new Uint8Array(Math.max(1, Math.min(capacity, MAX_TRACKED_LABEL_ID)) + 2);
  }

  /** Ids outside the bitset cannot be the answer, so they are simply dropped. */
  add(id: number): void {
    if (id >= 1 && id < this.taken.length && Number.isInteger(id)) this.taken[id] = 1;
  }

  /** Bulk form for a label plane — the hot path, so no per-id function call. */
  addAll(ids: ArrayLike<number> | null | undefined): void {
    if (!ids) return;
    const limit = this.taken.length;
    for (let i = 0; i < ids.length; i++) {
      const id = ids[i];
      if (id >= 1 && id < limit) this.taken[id] = 1;
    }
  }

  smallestFree(): number {
    for (let id = 1; id < this.taken.length; id++) {
      if (!this.taken[id]) return id;
    }
    return this.taken.length;
  }
}

/** The smallest free id given everything currently occupying the id space. */
export function nextFreshLabelId({
  summaryIds,
  trackParentIds,
  planes,
}: {
  /** Ids the server reports as present in the working copy. */
  summaryIds: readonly number[];
  /** Parent classes queued in Track — reserved even with no voxels yet. */
  trackParentIds: readonly number[];
  /** The live buffer plus every unsaved pending plane. */
  planes: readonly (ArrayLike<number> | null | undefined)[];
}): number {
  const used = new UsedLabelIds(
    usedLabelIdCapacity(
      summaryIds.length,
      trackParentIds.length,
      ...planes.map((plane) => plane?.length ?? 0),
    ),
  );
  for (const id of summaryIds) used.add(id);
  for (const id of trackParentIds) used.add(id);
  for (const plane of planes) used.addAll(plane);
  return used.smallestFree();
}
