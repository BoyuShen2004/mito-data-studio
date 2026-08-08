export type PendingSliceSnapshot = {
  index: number;
  ids: Int32Array;
  revision: number;
};

type PendingSliceEntry = {
  ids: Int32Array;
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

  markChanged(index: number, ids: Int32Array) {
    const revision = this.nextRevision++;
    // Always detach from the live canvas buffer — in-place tool mutations
    // (flood fill, brush) must not mutate a pending entry after Undo.
    this.entries.set(index, { ids: ids.slice(), revision });
    return revision;
  }

  /** Freeze an already-dirty live buffer before its canvas is reused. */
  freeze(index: number, ids: Int32Array) {
    const entry = this.entries.get(index);
    if (!entry) return false;
    this.entries.set(index, { ids: ids.slice(), revision: entry.revision });
    return true;
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
