import type { Axis } from "../../api/viewer";

/** Cellable-parity view axes. Default remains Axial (z) — XY plane. */
export const VIEW_AXIS_OPTIONS: readonly {
  value: Axis;
  label: string;
  short: string;
  title: string;
}[] = [
  { value: "z", label: "Axial (z)", short: "z", title: "Axial — scroll along z (XY plane)" },
  { value: "y", label: "Coronal (y)", short: "y", title: "Coronal — scroll along y (XZ plane)" },
  { value: "x", label: "Sagittal (x)", short: "x", title: "Sagittal — scroll along x (YZ plane)" },
] as const;

export const DEFAULT_VIEW_AXIS: Axis = "z";

export function axisLength(
  shape: { z: number; y: number; x: number } | undefined,
  axis: Axis,
): number {
  if (!shape) return 1;
  return Math.max(1, shape[axis] ?? 1);
}

/** Map a click on the current 2D slice (row=py, col=px) to volume (z, y, x).
 * Matches `slice_io.read_slice`: z→(y,x), y→(z,x), x→(z,y). */
export function voxelFromSlice(
  axis: Axis,
  index: number,
  py: number,
  px: number,
): { z: number; y: number; x: number } {
  if (axis === "z") return { z: index, y: py, x: px };
  if (axis === "y") return { z: py, y: index, x: px };
  return { z: py, y: px, x: index };
}

/** Project a volume voxel onto the current view, or null if it is off-slice. */
export function sliceCoordsFromVoxel(
  axis: Axis,
  index: number,
  voxel: { z: number; y: number; x: number },
): { py: number; px: number } | null {
  if (axis === "z") {
    if (voxel.z !== index) return null;
    return { py: voxel.y, px: voxel.x };
  }
  if (axis === "y") {
    if (voxel.y !== index) return null;
    return { py: voxel.z, px: voxel.x };
  }
  if (voxel.x !== index) return null;
  return { py: voxel.z, px: voxel.y };
}

export function axisShortLabel(axis: Axis): string {
  return VIEW_AXIS_OPTIONS.find((o) => o.value === axis)?.short ?? axis;
}
