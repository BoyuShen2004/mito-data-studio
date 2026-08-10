/**
 * "Jump to region": which planes of the current axis actually hold ROI, and
 * which of them is nearest.
 *
 * The list is keyed by (volume, axis). An in-memory map de-dupes concurrent
 * clicks and `sessionStorage` is a fast fallback within the tab, while each
 * viewer mount/axis change forces a server revalidation so a rebuilt mask
 * cannot leave navigation on stale planes. It is deliberately not
 * `localStorage`: a region mask replacement must not survive the tab.
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
  options: { refresh?: boolean } = {},
): Promise<number[]> {
  const key = cacheKey(volumeId, axis);
  const inflight = memory.get(key);
  if (inflight && !options.refresh) return inflight;
  const stored = options.refresh ? null : readSession(key);
  if (stored !== null) {
    const resolved = Promise.resolve(stored);
    memory.set(key, resolved);
    return resolved;
  }
  const request = fetcher(volumeId, axis)
    .then((response) => {
      if (response.axis !== axis) {
        throw new Error(`Region index returned axis ${response.axis}; expected ${axis}`);
      }
      const upper = Number.isInteger(response.axis_length)
        ? Number(response.axis_length)
        : Infinity;
      const indices = [...new Set(response.indices ?? [])]
        .filter((value) => Number.isInteger(value) && value >= 0 && value < upper)
        .sort((a, b) => a - b);
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
