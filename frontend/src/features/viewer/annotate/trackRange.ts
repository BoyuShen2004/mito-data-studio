import type { TrackingPrompt } from "../../../api/viewer";

/**
 * Start/End validation for the Track rail.
 *
 * **Numbering.** The API stores 0-based z indices; the viewer — its z field,
 * its `z N/M` readout, its tooltips — shows 1-based *layer* numbers. The Start
 * and End fields are part of the viewer, so they show layer numbers too, and
 * `toLayer`/`toZ` are the only place that conversion happens.
 *
 * **Inclusive.** `[start_z, end_z]` includes both endpoints, so a range of
 * `start_z === end_z` propagates exactly one layer.
 *
 * These checks mirror the backend's (`annotation.tracking.services.
 * validate_z_range` + `assert_seeds_within_range`). The backend is the
 * authority; this exists so the rail can explain the problem and keep
 * Propagate disabled instead of letting the user fire a request that will
 * bounce.
 */

/** 0-based API z → 1-based layer number shown in the viewer. */
export const toLayer = (z: number): number => z + 1;

/** 1-based layer number typed by the user → 0-based API z. */
export const toZ = (layer: number): number => layer - 1;

/** Every layer with at least one committed seed pixel, ascending. */
export function promptSeedZs(prompt: TrackingPrompt): number[] {
  return Array.from(
    new Set(prompt.subclasses.flatMap((child) => child.seeds.map((seed) => seed.z))),
  ).sort((a, b) => a - b);
}

export function promptHasSeeds(prompt: TrackingPrompt): boolean {
  return prompt.subclasses.some((child) => child.seeds.length > 0);
}

/**
 * Why this prompt cannot be propagated, or `null` when it can.
 *
 * `layerCount` is the volume's z depth; pass `0` when it is not known yet, and
 * the bounds check against the far end is skipped (the backend still enforces
 * it).
 */
export function trackRangeIssue(
  prompt: TrackingPrompt | null | undefined,
  layerCount: number,
): string | null {
  if (!prompt) return "Select a queued parent class first.";
  const { start_z: startZ, end_z: endZ } = prompt;
  if (startZ == null || endZ == null) {
    return "Set both Start and End layers before propagating.";
  }
  if (!Number.isInteger(startZ) || !Number.isInteger(endZ)) {
    return "Start and End must be whole layer numbers.";
  }
  if (startZ < 0) return `Start layer ${toLayer(startZ)} is before layer 1.`;
  if (layerCount > 0 && endZ >= layerCount) {
    return `End layer ${toLayer(endZ)} is past the last layer (${layerCount}).`;
  }
  if (endZ < startZ) {
    return `End layer ${toLayer(endZ)} must not be before Start layer ${toLayer(startZ)}.`;
  }
  if (!promptHasSeeds(prompt)) {
    return "Draw at least one child-class seed before propagating.";
  }
  const outside = promptSeedZs(prompt).filter((z) => z < startZ || z > endZ);
  if (outside.length) {
    const listed = outside.map(toLayer).join(", ");
    const [noun, verb] = outside.length === 1 ? ["layer", "falls"] : ["layers", "fall"];
    return `Seed ${noun} ${listed} ${verb} outside `
      + `${toLayer(startZ)}–${toLayer(endZ)} (inclusive). Widen the range or clear those seeds.`;
  }
  return null;
}

export const canPropagatePrompt = (
  prompt: TrackingPrompt | null | undefined,
  layerCount: number,
): boolean => trackRangeIssue(prompt, layerCount) === null;
