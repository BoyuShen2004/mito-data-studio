/**
 * Client-side flood fill — same semantics as the Phase 9 server tool, but
 * applied to in-memory label rasters so Undo works and nothing is auto-saved.
 *
 * Connectivity: 4 in-plane / 6 with z (no diagonal leak).
 * Overwrite: `overwrite_empty` writes only background; `overwrite_all` replaces
 * every voxel in the seed's connected component.
 */

export type LocalOverwriteMode = "overwrite_empty" | "overwrite_all";

const idx = (z: number, y: number, x: number, h: number, w: number) =>
  z * h * w + y * w + x;

/**
 * Mutates `block` (z-major flat length `d*h*w`). Returns voxels actually written.
 */
export function floodFillBlock(
  block: Int32Array,
  d: number,
  h: number,
  w: number,
  seedZ: number,
  seedY: number,
  seedX: number,
  labelId: number,
  overwriteMode: LocalOverwriteMode,
): number {
  if (d < 1 || h < 1 || w < 1 || block.length !== d * h * w) {
    throw new Error("floodFillBlock: shape does not match buffer length");
  }
  if (
    seedZ < 0 ||
    seedZ >= d ||
    seedY < 0 ||
    seedY >= h ||
    seedX < 0 ||
    seedX >= w
  ) {
    return 0;
  }
  if (labelId < 1) return 0;

  const seedI = idx(seedZ, seedY, seedX, h, w);
  const seedLabel = block[seedI];
  if (seedLabel === labelId) return 0;
  if (overwriteMode === "overwrite_empty" && seedLabel !== 0) return 0;

  const visited = new Uint8Array(block.length);
  // A typed FIFO is bounded at 4 bytes/voxel. The old pair of growing JS
  // number arrays (`queue` + `component`) could consume tens of bytes per
  // voxel and freeze/OOM the tab on a large background component.
  const queue = new Int32Array(block.length);
  let head = 0;
  let tail = 1;
  queue[0] = seedI;
  visited[seedI] = 1;
  let changed = 0;

  while (head < tail) {
    const i = queue[head++];
    const z = Math.floor(i / (h * w));
    const rem = i - z * h * w;
    const y = Math.floor(rem / w);
    const x = rem - y * w;
    const step = (
      nz: number,
      ny: number,
      nx: number,
    ) => {
      if (nz < 0 || nz >= d || ny < 0 || ny >= h || nx < 0 || nx >= w) return;
      const ni = idx(nz, ny, nx, h, w);
      if (visited[ni]) return;
      if (block[ni] !== seedLabel) return;
      visited[ni] = 1;
      queue[tail++] = ni;
    };
    step(z, y, x + 1);
    step(z, y, x - 1);
    step(z, y + 1, x);
    step(z, y - 1, x);
    step(z + 1, y, x);
    step(z - 1, y, x);
    // Neighbours were discovered against the original component value before
    // this write, so painting in place needs no second component-sized list.
    if (block[i] !== labelId) {
      block[i] = labelId;
      changed += 1;
    }
  }
  return changed;
}

/** Depth-1 convenience for a single plane (`h*w`). */
export function floodFillPlane(
  plane: Int32Array,
  h: number,
  w: number,
  row: number,
  col: number,
  labelId: number,
  overwriteMode: LocalOverwriteMode,
): number {
  return floodFillBlock(plane, 1, h, w, 0, row, col, labelId, overwriteMode);
}
