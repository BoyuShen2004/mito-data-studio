/**
 * "Jump to region": which planes of the current axis actually hold ROI, and
 * which of them is nearest.
 *
 * The list is a property of the volume's immutable region mask, so it is
 * fetched at most once per (volume, axis) per browser session — an in-memory
 * map de-dupes concurrent clicks, and `sessionStorage` carries the answer
 * across page navigations within the tab (View -> Annotate -> back). It is
 * deliberately *not* `localStorage`: a region mask that is replaced on the
 * server must not be answered for out of a cache that outlives the tab.
 */

import type { Axis, RegionIndex } from "../../api/viewer";

const memory = new Map<string, Promise<number[]>>();

const cacheKey = (volumeId: number, axis: Axis) => `mito.region-index.${volumeId}.${axis}`;

function readSession(key: string): number[] | null {
  try {
    const raw = window.sessionStorage?.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) && parsed.every((n) => typeof n === "number")
      ? parsed
      : null;
  } catch {
    // A private-mode / quota-disabled sessionStorage is not a reason to fail
    // the feature; it just means every navigation pays for one more request.
    return null;
  }
}

function writeSession(key: string, indices: number[]): void {
  try {
    window.sessionStorage?.setItem(key, JSON.stringify(indices));
  } catch {
    /* see readSession */
  }
}

/** Fetch (or recall) the ROI-bearing plane indices for one volume + axis. */
export function loadRegionIndex(
  volumeId: number,
  axis: Axis,
  fetcher: (volumeId: number, axis: Axis) => Promise<RegionIndex>,
): Promise<number[]> {
  const key = cacheKey(volumeId, axis);
  const inflight = memory.get(key);
  if (inflight) return inflight;
  const stored = readSession(key);
  if (stored) {
    const resolved = Promise.resolve(stored);
    memory.set(key, resolved);
    return resolved;
  }
  const request = fetcher(volumeId, axis)
    .then((response) => {
      const indices = response.indices ?? [];
      writeSession(key, indices);
      return indices;
    })
    .catch((error) => {
      // Never cache a failure: the next click should retry, not repeat it.
      memory.delete(key);
      throw error;
    });
  memory.set(key, request);
  return request;
}

/** Drop the session cache (tests, and a volume whose mask was rebuilt). */
export function clearRegionIndexCache(): void {
  for (const key of memory.keys()) {
    try {
      window.sessionStorage?.removeItem(key);
    } catch {
      /* see readSession */
    }
  }
  memory.clear();
}

/**
 * The ROI-bearing plane nearest to `current`, or `null` when there is nothing
 * to jump to — either because `current` already holds region (the documented
 * no-op) or because no plane does.
 *
 * Ties go to the lower index, so the button is deterministic: from exactly
 * between two ROI blocks it always steps back, never wobbles.
 */
export function nearestRegionIndex(
  indices: readonly number[],
  current: number,
): number | null {
  let best: number | null = null;
  let bestDistance = Infinity;
  for (const index of indices) {
    if (index === current) return null;
    const distance = Math.abs(index - current);
    if (distance < bestDistance || (distance === bestDistance && index < (best ?? Infinity))) {
      best = index;
      bestDistance = distance;
    }
  }
  return best;
}
