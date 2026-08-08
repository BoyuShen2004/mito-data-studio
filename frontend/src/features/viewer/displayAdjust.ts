/** Brightness / contrast knobs on a clear 0–100 scale (50 = neutral). */
export function clampPct(n: number): number {
  if (Number.isNaN(n)) return 50;
  return Math.max(0, Math.min(100, Math.round(n)));
}

/** CSS filter from 0–100 brightness & contrast (50 → identity). */
export function displayFilter(brightness: number, contrast: number): string {
  const b = Math.max(0.01, brightness / 50);
  const c = Math.max(0.01, contrast / 50);
  return `brightness(${b}) contrast(${c})`;
}
