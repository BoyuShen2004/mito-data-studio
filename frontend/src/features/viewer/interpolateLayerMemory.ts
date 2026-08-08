import type { Axis } from "../../api/viewer";

export type InterpolateLayerMemory = Map<string, number[]>;

const keyFor = (axis: Axis, labelId: number) => `${axis}:${labelId}`;

/**
 * Remember successful positive-label commits in recency order. Re-touching a
 * layer moves it to the end. When queried, the newest layer is paired with the
 * newest earlier layer at least two planes away; adjacent touches are retained
 * but skipped, so they do not erase the last usable interpolation pair.
 */
export function rememberLabelLayer(
  memory: InterpolateLayerMemory,
  axis: Axis,
  labelId: number,
  layer: number,
): void {
  if (labelId < 1 || layer < 0) return;
  const key = keyFor(axis, labelId);
  const previous = memory.get(key) ?? [];
  const next = previous.filter((value) => value !== layer);
  next.push(layer);
  // A small recency tail is enough to skip adjacent layers without retaining
  // an unbounded annotation-session log.
  memory.set(key, next.slice(-32));
}

export function rememberedNonAdjacentPair(
  memory: InterpolateLayerMemory,
  axis: Axis,
  labelId: number,
): [number, number] | null {
  const layers = memory.get(keyFor(axis, labelId)) ?? [];
  if (layers.length < 2) return null;
  const newest = layers[layers.length - 1];
  for (let i = layers.length - 2; i >= 0; i -= 1) {
    const earlier = layers[i];
    if (Math.abs(newest - earlier) >= 2) {
      return [Math.min(earlier, newest), Math.max(earlier, newest)];
    }
  }
  return null;
}
