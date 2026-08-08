/**
 * Per-slice undo/redo for label rasters, plus compound multi-slice ops
 * (interpolate / flood fill) that undo as a single unit from any slice.
 *
 * Slice-local history is keyed by index. Stash/restore always copy the stack
 * containers so a pop/push on the live stack cannot empty the parked history
 * of another slice.
 *
 * Compound ops live on a global stack that stash/restore do not touch — they
 * must survive z-navigation and must not compete with the 160MB per-slice
 * parking budget (which previously dropped interpolate undos on large EM).
 *
 * Entries are only recorded when pixels actually change.
 */

export const MAX_SLICE_UNDO = 20;
export const MAX_HISTORY_BYTES = 160 * 1024 * 1024;

export function historyEntryLimit(byteLength: number): number {
  if (byteLength <= 0) return MAX_SLICE_UNDO;
  return Math.max(
    1,
    Math.min(MAX_SLICE_UNDO, Math.floor(MAX_HISTORY_BYTES / byteLength)),
  );
}

function rastersEqual(a: Int32Array, b: Int32Array): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

type SliceStacks = {
  undo: Int32Array[];
  redo: Int32Array[];
  /** Monotonic clocks aligned 1:1 with undo/redo entries (same length). */
  undoClocks: number[];
  redoClocks: number[];
};

export type CompoundSliceEdit = {
  index: number;
  before: Int32Array;
  after: Int32Array;
};

export type UndoResult =
  | { kind: "slice"; raster: Int32Array }
  | { kind: "compound"; slices: CompoundSliceEdit[] };

export class SliceHistory {
  private undoStack: Int32Array[] = [];
  private redoStack: Int32Array[] = [];
  private undoClocks: number[] = [];
  private redoClocks: number[] = [];
  private readonly byIndex = new Map<number, SliceStacks>();
  private baseline: Int32Array | null = null;
  private clock = 0;

  private compoundUndo: { clock: number; slices: CompoundSliceEdit[] }[] = [];
  private compoundRedo: { clock: number; slices: CompoundSliceEdit[] }[] = [];

  get undoCount() {
    return this.undoStack.length + this.compoundUndo.length;
  }

  get redoCount() {
    return this.redoStack.length + this.compoundRedo.length;
  }

  clearAll() {
    this.byIndex.clear();
    this.undoStack = [];
    this.redoStack = [];
    this.undoClocks = [];
    this.redoClocks = [];
    this.compoundUndo = [];
    this.compoundRedo = [];
    this.baseline = null;
    this.clock = 0;
  }

  /** Park the live stacks under `index` without sharing the array containers. */
  stash(index: number) {
    this.byIndex.delete(index);
    this.byIndex.set(index, {
      undo: this.undoStack.slice(),
      redo: this.redoStack.slice(),
      undoClocks: this.undoClocks.slice(),
      redoClocks: this.redoClocks.slice(),
    });
    this.trimParked();
    this.baseline = null;
  }

  /** Replace the live stacks with a fresh copy of whatever was parked. */
  restore(index: number) {
    const parked = this.byIndex.get(index);
    this.undoStack = parked ? parked.undo.slice() : [];
    this.redoStack = parked ? parked.redo.slice() : [];
    this.undoClocks = parked ? parked.undoClocks.slice() : [];
    this.redoClocks = parked ? parked.redoClocks.slice() : [];
    this.baseline = null;
  }

  /**
   * Begin a multi-pointer stroke. The baseline is kept off the undo stack
   * until `commitStroke` proves the pixels changed.
   */
  beginStroke(ids: Int32Array) {
    this.baseline = ids.slice();
  }

  /**
   * Finish a stroke. Returns true when an undo entry was recorded.
   * A no-op stroke (click without changing any pixel) is discarded.
   */
  commitStroke(ids: Int32Array): boolean {
    const before = this.baseline;
    this.baseline = null;
    if (!before) return false;
    if (rastersEqual(before, ids)) return false;
    this.pushUndo(before);
    return true;
  }

  /** Abort an in-progress stroke without recording history. */
  cancelStroke() {
    this.baseline = null;
  }

  get hasOpenStroke() {
    return this.baseline != null;
  }

  /**
   * Record `before` as the state to restore on Undo, but only when it differs
   * from `after` (the state already written into the live buffer).
   */
  recordChange(before: Int32Array, after: Int32Array): boolean {
    if (rastersEqual(before, after)) return false;
    this.pushUndo(before);
    return true;
  }

  /**
   * Record one multi-slice edit (interpolate / flood) as a single undo step.
   * Survives stash/restore so Undo stays available after jumping z.
   */
  recordCompound(edits: CompoundSliceEdit[]): boolean {
    const slices = edits.filter((e) => !rastersEqual(e.before, e.after));
    if (slices.length === 0) return false;
    this.clock += 1;
    this.compoundUndo.push({ clock: this.clock, slices });
    this.compoundRedo = [];
    // A new global edit invalidates slice-local redo on the live stack.
    this.redoStack = [];
    this.redoClocks = [];
    return true;
  }

  /**
   * Convenience for in-place mutators: snapshot, run `mutate`, keep the
   * snapshot only when something changed.
   */
  withChange(ids: Int32Array, mutate: () => void): boolean {
    const before = ids.slice();
    mutate();
    return this.recordChange(before, ids);
  }

  undo(current: Int32Array): UndoResult | null {
    this.baseline = null;
    const sliceClock = this.undoClocks.length
      ? this.undoClocks[this.undoClocks.length - 1]
      : -1;
    const compoundTop = this.compoundUndo[this.compoundUndo.length - 1];
    const compoundClock = compoundTop ? compoundTop.clock : -1;

    if (compoundClock > sliceClock) {
      const entry = this.compoundUndo.pop()!;
      this.compoundRedo.push(entry);
      return { kind: "compound", slices: entry.slices };
    }

    const prev = this.undoStack.pop();
    const clock = this.undoClocks.pop();
    if (!prev || clock == null) return null;
    this.redoStack.push(current.slice());
    this.redoClocks.push(clock);
    this.trimStack(this.redoStack, this.redoClocks, current.byteLength);
    return { kind: "slice", raster: prev };
  }

  redo(current: Int32Array): UndoResult | null {
    this.baseline = null;
    const sliceClock = this.redoClocks.length
      ? this.redoClocks[this.redoClocks.length - 1]
      : -1;
    const compoundTop = this.compoundRedo[this.compoundRedo.length - 1];
    const compoundClock = compoundTop ? compoundTop.clock : -1;

    if (compoundClock > sliceClock) {
      const entry = this.compoundRedo.pop()!;
      this.compoundUndo.push(entry);
      return { kind: "compound", slices: entry.slices };
    }

    const next = this.redoStack.pop();
    const clock = this.redoClocks.pop();
    if (!next || clock == null) return null;
    this.undoStack.push(current.slice());
    this.undoClocks.push(clock);
    this.trimStack(this.undoStack, this.undoClocks, current.byteLength);
    return { kind: "slice", raster: next };
  }

  private pushUndo(snapshot: Int32Array) {
    this.clock += 1;
    this.undoStack.push(snapshot);
    this.undoClocks.push(this.clock);
    this.trimStack(this.undoStack, this.undoClocks, snapshot.byteLength);
    this.redoStack = [];
    this.redoClocks = [];
    // Slice edit is newer than any pending compound redo.
    this.compoundRedo = [];
  }

  private trimStack(
    stack: Int32Array[],
    clocks: number[],
    byteLength: number,
  ) {
    const limit = historyEntryLimit(byteLength);
    while (stack.length > limit) {
      stack.shift();
      clocks.shift();
    }
  }

  private trimParked() {
    const bytes = () => {
      let total = 0;
      for (const stacks of this.byIndex.values()) {
        for (const raster of stacks.undo) total += raster.byteLength;
        for (const raster of stacks.redo) total += raster.byteLength;
      }
      return total;
    };
    while (this.byIndex.size > 1 && bytes() > MAX_HISTORY_BYTES) {
      const oldest = this.byIndex.keys().next().value as number;
      this.byIndex.delete(oldest);
    }
  }
}
