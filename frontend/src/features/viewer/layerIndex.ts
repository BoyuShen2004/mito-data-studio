/** Human-facing layer / z numbers are 1-based; storage and APIs stay 0-based. */

export function displayLayer(zeroBased: number): number {
  return zeroBased + 1;
}

/** Format an inclusive 0-based layer span for UI (e.g. "1–10"). */
export function displayLayerRange(start: number, end: number): string {
  const a = displayLayer(start);
  const b = displayLayer(end);
  return a === b ? String(a) : `${a}–${b}`;
}

/** Format a task's half-open 0-based z span `[start, end)` for 1-based UI. */
export function displayTaskLayerRange(start: number, end: number): string {
  const first = displayLayer(start);
  const last = Math.max(first, end);
  return first === last ? String(first) : `${first}–${last}`;
}

/** Parse a 1-based layer input into a 0-based index, or null if empty/invalid. */
export function parseLayerInput(raw: string): number | null {
  if (raw === "") return null;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 1) return null;
  return Math.floor(n) - 1;
}
