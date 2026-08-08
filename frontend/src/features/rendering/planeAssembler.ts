import type {
  ChunkDType,
  ChunkLevel,
  ChunkTypedArray,
  DecodedChunk,
} from "../chunks/types";
import type { Axis } from "../../api/viewer";

export interface AssembledPlane {
  axis: Axis;
  levelIndex: number;
  shape: [number, number];
  values: ChunkTypedArray;
}

export function allocateValues(
  dtype: ChunkDType,
  length: number,
): ChunkTypedArray {
  switch (dtype) {
    case "uint8": return new Uint8Array(length);
    case "int8": return new Int8Array(length);
    case "uint16": return new Uint16Array(length);
    case "int16": return new Int16Array(length);
    case "uint32": return new Uint32Array(length);
    case "int32": return new Int32Array(length);
    case "float32": return new Float32Array(length);
    case "float64": return new Float64Array(length);
  }
}

/**
 * Assemble one orthogonal plane from Z/Y/X row-major chunks. The returned
 * orientation matches the established TIFF endpoints:
 *   z (XY) => rows Y, columns X
 *   y (XZ) => rows Z, columns X
 *   x (YZ) => rows Z, columns Y
 */
export function assemblePlane(
  axis: Axis,
  levelIndex: number,
  level: ChunkLevel,
  chunks: readonly DecodedChunk[],
): AssembledPlane {
  const [depth, height, width] = level.shape;
  const shape: [number, number] =
    axis === "z" ? [height, width] :
    axis === "y" ? [depth, width] :
    [depth, height];
  const output = allocateValues(level.dtype, shape[0] * shape[1]);

  for (const chunk of chunks) {
    const [chunkDepth, chunkHeight, chunkWidth] = chunk.shape;
    const [z0, y0, x0] = chunk.voxelOffset;
    const planeStride = chunkHeight * chunkWidth;
    if (axis === "z") {
      const localZ = levelIndex - z0;
      if (localZ < 0 || localZ >= chunkDepth) continue;
      for (let y = 0; y < chunkHeight; y += 1) {
        const source = localZ * planeStride + y * chunkWidth;
        output.set(
          chunk.values.subarray(source, source + chunkWidth),
          (y0 + y) * width + x0,
        );
      }
    } else if (axis === "y") {
      const localY = levelIndex - y0;
      if (localY < 0 || localY >= chunkHeight) continue;
      for (let z = 0; z < chunkDepth; z += 1) {
        const source = z * planeStride + localY * chunkWidth;
        output.set(
          chunk.values.subarray(source, source + chunkWidth),
          (z0 + z) * width + x0,
        );
      }
    } else {
      const localX = levelIndex - x0;
      if (localX < 0 || localX >= chunkWidth) continue;
      for (let z = 0; z < chunkDepth; z += 1) {
        for (let y = 0; y < chunkHeight; y += 1) {
          output[(z0 + z) * height + y0 + y] =
            chunk.values[z * planeStride + y * chunkWidth + localX];
        }
      }
    }
  }
  return { axis, levelIndex, shape, values: output };
}

export function sourcePlaneShape(
  axis: Axis,
  shape: { z: number; y: number; x: number },
): [number, number] {
  if (axis === "z") return [shape.y, shape.x];
  if (axis === "y") return [shape.z, shape.x];
  return [shape.z, shape.y];
}
