import type { TrackingPrompt, TrackingSubclass } from "../../../api/viewer";

export interface TrackingPromptGeometrySnapshot {
  parents: Array<{
    parentId: number;
    children: Array<{
      index: number;
      seeds: TrackingSubclass["seeds"];
    }>;
  }>;
}

const cloneSeeds = (seeds: TrackingSubclass["seeds"]): TrackingSubclass["seeds"] =>
  seeds.map((seed) => ({
    ...seed,
    shape: [...seed.shape] as [number, number],
    rle: seed.rle.map(([start, length]) => [start, length]),
  }));

export function snapshotTrackingPromptGeometry(
  prompts: TrackingPrompt[],
): TrackingPromptGeometrySnapshot {
  return {
    parents: prompts.map((prompt) => ({
      parentId: prompt.parent_id,
      children: prompt.subclasses.map((child) => ({
        index: child.index,
        seeds: cloneSeeds(child.seeds),
      })),
    })),
  };
}

/** Restore only seed-mask geometry onto the current queue structure.
 *
 * Parents and children that were added after the snapshot stay present, while
 * removed queue members are never resurrected. This keeps Track Undo/Redo
 * scoped to prompt edits even when queue membership changes between edits.
 */
export function restoreTrackingPromptGeometry(
  current: TrackingPrompt[],
  snapshot: TrackingPromptGeometrySnapshot,
): TrackingPrompt[] {
  const parents = new Map(snapshot.parents.map((parent) => [parent.parentId, parent]));
  return current.map((prompt) => {
    const savedParent = parents.get(prompt.parent_id);
    if (!savedParent) return prompt;
    const children = new Map(savedParent.children.map((child) => [child.index, child]));
    let restoredAny = false;
    const subclasses = prompt.subclasses.map((child) => {
      const savedChild = children.get(child.index);
      if (!savedChild) return child;
      restoredAny = true;
      return { ...child, seeds: cloneSeeds(savedChild.seeds) };
    });
    if (!restoredAny) return prompt;
    // Prompt Undo/Redo is scoped to seed geometry. The explicit Start/End range
    // is a separate, deliberate choice and is deliberately left alone — undoing
    // a brush stroke must not also move the propagation bounds.
    return {
      ...prompt,
      subclasses,
      status: subclasses.some((child) => child.seeds.length) ? "ready" : "draft",
    };
  });
}
