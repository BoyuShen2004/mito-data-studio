/**
 * Edits made *outside* the region mask while Region only was ON.
 *
 * ## The model
 *
 * Region only used to mean "you cannot see or keep anything outside the ROI".
 * It means "the ROI is what you are focused on". Paint may be staged on empty
 * voxels outside it, but Region-only display remains strict: only instances
 * that touch the ROI are visible, shown whole.
 *
 * Three pieces make that work, and only the third is new:
 *
 *  1. `idsRef` — the raw painted plane. Toggling Region only **never** rewrites
 *     it. That is the whole reason toggling cannot destroy pending work.
 *  2. The decoded ROI bitmap (`regionOverlap.ts`) — which pixels are inside.
 *  3. This record — for each pixel painted outside the ROI while Region only
 *     was on, the value the *stored* label held there before the stroke.
 *
 * It is sparse (a flat index plus the baseline value per pixel) rather than a
 * second full plane. A full `Int32Array` per pending slice is what turned an
 * unsaved-edit buffer into hundreds of megabytes and cost an annotator their
 * afternoon — see the data-loss incident in `DEPLOYMENT.md`. Outside strokes
 * are small; this stays proportional to them.
 *
 * ## What the record is used for
 *
 * *Presentation on the way out.* Leaving Region only is the moment those
 * outside edits meet the labels that were already there, and `overwriteMode`
 * decides that meeting — exactly as it does for Interpolate and Flood fill:
 *
 *   - `overwrite_all`   — the edit stands everywhere.
 *   - `overwrite_empty` — the edit stands only where the stored label was
 *                         empty; where it painted (or erased) over an existing
 *                         instance, the stored value is what is presented.
 *
 * This is a *projection*, never a rewrite of the buffer. Switching the mode, or
 * switching Region only back on, re-derives the view from the same untouched
 * paint — which is what "toggling must not wipe pending outside work" requires.
 */

import type { OverwriteMode } from "../../api/viewer";

export interface OutsideEditRecord {
  /** Flat `y * width + x` index into the plane. */
  index: number;
  /** What the stored label held here before Region only edits touched it. */
  baseline: number;
}

export class OutsideRegionEdits {
  /** index -> baseline. The first record for a pixel wins: a pixel painted
   * twice still has exactly one "what was here before" value. */
  private readonly baselines = new Map<number, number>();

  get size() {
    return this.baselines.size;
  }

  get isEmpty() {
    return this.baselines.size === 0;
  }

  has(index: number) {
    return this.baselines.has(index);
  }

  /** Note that `index` was painted outside the ROI, over `baseline`. */
  record(index: number, baseline: number) {
    if (!this.baselines.has(index)) this.baselines.set(index, baseline);
  }

  records(): OutsideEditRecord[] {
    return [...this.baselines].map(([index, baseline]) => ({ index, baseline }));
  }

  /** A detached copy — freezing a plane must not alias the live record. */
  clone(): OutsideRegionEdits {
    const copy = new OutsideRegionEdits();
    for (const [index, baseline] of this.baselines) copy.baselines.set(index, baseline);
    return copy;
  }

  clear() {
    this.baselines.clear();
  }

  /** Refresh sparse stored values after conflict recovery loads newer disk. */
  rebase(serverIds: Int32Array) {
    for (const index of this.baselines.keys()) {
      if (index < serverIds.length) this.baselines.set(index, serverIds[index]);
    }
  }

  /**
   * The plane as it should be *presented* (and saved) right now.
   *
   * Returns `ids` itself when nothing would change, so the common case — no
   * outside edits at all, i.e. anyone who never turned Region only on — costs
   * one map-size check and allocates nothing.
   */
  project(
    ids: Int32Array,
    { regionOnly, overwriteMode }: { regionOnly: boolean; overwriteMode: OverwriteMode },
  ): Int32Array {
    // While Region only is on, outside paint is shown exactly as drawn: the
    // annotator is still working on it and has not yet asked how it should
    // meet the stored labels.
    if (this.isEmpty || regionOnly || overwriteMode === "overwrite_all") return ids;
    let projected: Int32Array | null = null;
    for (const [index, baseline] of this.baselines) {
      // Empty-voxels-only: an edit that landed on an existing label loses to
      // it; one that landed on background stands.
      if (baseline === 0 || index >= ids.length || ids[index] === baseline) continue;
      if (!projected) projected = ids.slice();
      projected[index] = baseline;
    }
    return projected ?? ids;
  }

  /**
   * How many recorded pixels still differ from what the server holds.
   *
   * A record survives Undo, so "there are outside edits" and "there is outside
   * work to lose" are different questions — this answers the second, which is
   * the one Save should warn about. Undo everything and it returns 0.
   */
  pendingCount(ids: Int32Array): number {
    let count = 0;
    for (const [index, baseline] of this.baselines) {
      if (index < ids.length && ids[index] !== baseline) count += 1;
    }
    return count;
  }
}

/** Per-plane records, keyed exactly like the pending-slice buffer.
 *
 * Cleared with it on an axis switch: an index means a different plane there. */
export class OutsideRegionEditStore {
  private readonly planes = new Map<number, OutsideRegionEdits>();

  /** The live record for a plane, created on first use. */
  for(index: number): OutsideRegionEdits {
    let edits = this.planes.get(index);
    if (!edits) {
      edits = new OutsideRegionEdits();
      this.planes.set(index, edits);
    }
    return edits;
  }

  /** The record for a plane, or undefined — never creates one. */
  peek(index: number): OutsideRegionEdits | undefined {
    return this.planes.get(index);
  }

  /** True when any plane holds outside edits (what Save has to warn about). */
  get totalSize(): number {
    let total = 0;
    for (const edits of this.planes.values()) total += edits.size;
    return total;
  }

  delete(index: number) {
    this.planes.delete(index);
  }

  rebase(index: number, serverIds: Int32Array) {
    this.planes.get(index)?.rebase(serverIds);
  }

  clear() {
    this.planes.clear();
  }
}
