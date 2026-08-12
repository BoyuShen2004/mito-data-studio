export type PendingSliceSnapshot = {
  index: number;
  ids: Int32Array;
  revision: number;
};

export type PendingSliceRebase = {
  reapplied: number;
  conflicts: number;
  pending: boolean;
};

type PendingSliceEntry = {
  ids: Int32Array;
  /** Original server value only for pixels this tab has changed. */
  baselines: Map<number, number>;
  revision: number;
};

/**
 * Tracks only slices whose pixels actually changed.
 *
 * Entries are revisioned so a save response can acknowledge exactly the
 * version it sent without deleting newer strokes made while that request was
 * in flight.
 */
export class PendingSliceBuffer {
  private readonly entries = new Map<number, PendingSliceEntry>();
  private nextRevision = 1;

  get size() {
    return this.entries.size;
  }

  has(index: number) {
    return this.entries.has(index);
  }

  get(index: number) {
    return this.entries.get(index)?.ids;
  }

  markChanged(index: number, ids: Int32Array, baseline: Int32Array) {
    if (ids.length !== baseline.length) {
      throw new Error(`Cannot mark layer ${index} changed: its shape changed.`);
    }
    const revision = this.nextRevision++;
    const existing = this.entries.get(index);
    const baselines = new Map(existing?.baselines);
    for (let offset = 0; offset < ids.length; offset += 1) {
      const original = baselines.get(offset);
      if (original !== undefined) {
        if (ids[offset] === original) baselines.delete(offset);
      } else if (ids[offset] !== baseline[offset]) {
        baselines.set(offset, baseline[offset]);
      }
    }
    // Always detach from the live canvas buffer — in-place tool mutations
    // (flood fill, brush) must not mutate a pending entry after Undo.
    this.entries.set(index, {
      ids: ids.slice(),
      // Sparse originals distinguish this tab's intent from untouched pixels
      // in the full-plane PUT without retaining a second full raster.
      baselines,
      revision,
    });
    return revision;
  }

  /** Freeze an already-dirty live buffer before its canvas is reused. */
  freeze(index: number, ids: Int32Array) {
    const entry = this.entries.get(index);
    if (!entry) return false;
    this.entries.set(index, {
      ids: ids.slice(),
      baselines: entry.baselines,
      revision: entry.revision,
    });
    return true;
  }

  /**
   * Reapply this tab's non-overlapping edits onto a newly loaded server plane.
   * A pixel changed by both sides keeps the newer server value; callers report
   * that count instead of silently turning recovery into a stale overwrite.
   */
  rebase(index: number, serverIds: Int32Array): PendingSliceRebase {
    const entry = this.entries.get(index);
    if (!entry) return { reapplied: 0, conflicts: 0, pending: false };
    if (entry.ids.length !== serverIds.length) {
      throw new Error(`Cannot rebase layer ${index}: its shape changed.`);
    }
    const rebased = serverIds.slice();
    const rebasedBaselines = new Map<number, number>();
    let reapplied = 0;
    let conflicts = 0;
    for (const [offset, baseline] of entry.baselines) {
      const local = entry.ids[offset];
      const server = serverIds[offset];
      if (server !== baseline && server !== local) {
        conflicts += 1;
        continue;
      }
      if (server !== local) {
        rebased[offset] = local;
        rebasedBaselines.set(offset, server);
        reapplied += 1;
      }
    }
    const pending = rebasedBaselines.size > 0;
    if (!pending) {
      this.entries.delete(index);
    } else {
      this.entries.set(index, {
        ids: rebased,
        baselines: rebasedBaselines,
        revision: this.nextRevision++,
      });
    }
    return { reapplied, conflicts, pending };
  }

  delete(index: number) {
    return this.entries.delete(index);
  }

  clear() {
    this.entries.clear();
  }

  snapshots(): PendingSliceSnapshot[] {
    return [...this.entries].map(([index, entry]) => ({
      index,
      ids: entry.ids.slice(),
      revision: entry.revision,
    }));
  }

  /**
   * Remove a successfully saved version only if no newer edit replaced it
   * while the request was running.
   */
  acknowledge(index: number, revision: number) {
    const current = this.entries.get(index);
    if (!current || current.revision !== revision) return false;
    this.entries.delete(index);
    return true;
  }

  values() {
    return [...this.entries.values()].map((entry) => entry.ids);
  }
}
