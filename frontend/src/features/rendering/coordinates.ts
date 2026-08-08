import type { Axis } from "../../api/viewer";

export interface CanvasTransform {
  zoom: number;
  panX: number;
  panY: number;
  devicePixelRatio: number;
}

export function renderedToPlane(
  cssX: number,
  cssY: number,
  transform: CanvasTransform,
): [number, number] {
  if (!(transform.zoom > 0) || !(transform.devicePixelRatio > 0)) {
    throw new RangeError("zoom and devicePixelRatio must be positive");
  }
  // CSS coordinates are independent of the backing-store DPR. DPR affects
  // raster sharpness, not annotation voxel identity.
  return [
    (cssX - transform.panX) / transform.zoom,
    (cssY - transform.panY) / transform.zoom,
  ];
}

export function planeToSourceVoxel(
  axis: Axis,
  index: number,
  column: number,
  row: number,
): [number, number, number] {
  if (axis === "z") return [index, row, column];
  if (axis === "y") return [row, index, column];
  return [row, column, index];
}

export function sourceVoxelToPlane(
  axis: Axis,
  voxel: readonly [number, number, number],
): [number, number, number] {
  const [z, y, x] = voxel;
  if (axis === "z") return [x, y, z];
  if (axis === "y") return [x, z, y];
  return [y, z, x];
}
