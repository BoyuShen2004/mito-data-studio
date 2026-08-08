import { describe, expect, it } from "vitest";
import type { ChunkLevel, DecodedChunk } from "../chunks/types";
import { assemblePlane, sourcePlaneShape } from "./planeAssembler";

const level: ChunkLevel = {
  mag: "1",
  shape: [3, 4, 5],
  chunks: [2, 3, 3],
  grid: [2, 2, 2],
  factors: [1, 1, 1],
  dtype: "uint16",
};

function chunks(): DecodedChunk[] {
  const result: DecodedChunk[] = [];
  for (let z0 = 0; z0 < 3; z0 += 2) {
    for (let y0 = 0; y0 < 4; y0 += 3) {
      for (let x0 = 0; x0 < 5; x0 += 3) {
        const shape: [number, number, number] = [
          Math.min(2, 3 - z0),
          Math.min(3, 4 - y0),
          Math.min(3, 5 - x0),
        ];
        const values = new Uint16Array(shape[0] * shape[1] * shape[2]);
        for (let z = 0; z < shape[0]; z += 1) {
          for (let y = 0; y < shape[1]; y += 1) {
            for (let x = 0; x < shape[2]; x += 1) {
              values[(z * shape[1] + y) * shape[2] + x] =
                (z0 + z) * 100 + (y0 + y) * 10 + x0 + x;
            }
          }
        }
        result.push({
          identity: {
            deployment: "test",
            volumeId: 1,
            buildIdentity: "build",
            mag: "1",
            chunk: [z0 / 2, y0 / 3, x0 / 3],
            dtype: "uint16",
            representation: "raw-le",
            authorizationScope: "test",
          },
          shape,
          voxelOffset: [z0, y0, x0],
          etag: "test",
          values,
          byteLength: values.byteLength,
          timing: { networkMs: 0, decodeMs: 0 },
        });
      }
    }
  }
  return result;
}

describe("orthogonal chunk plane assembly", () => {
  it("assembles XY without transposing cropped edge chunks", () => {
    const plane = assemblePlane("z", 2, level, chunks());
    expect(plane.shape).toEqual([4, 5]);
    expect([...plane.values]).toEqual([
      200, 201, 202, 203, 204,
      210, 211, 212, 213, 214,
      220, 221, 222, 223, 224,
      230, 231, 232, 233, 234,
    ]);
  });

  it("assembles XZ with rows Z and columns X", () => {
    const plane = assemblePlane("y", 3, level, chunks());
    expect(plane.shape).toEqual([3, 5]);
    expect([...plane.values]).toEqual([
      30, 31, 32, 33, 34,
      130, 131, 132, 133, 134,
      230, 231, 232, 233, 234,
    ]);
  });

  it("assembles YZ with rows Z and columns Y", () => {
    const plane = assemblePlane("x", 4, level, chunks());
    expect(plane.shape).toEqual([3, 4]);
    expect([...plane.values]).toEqual([
      4, 14, 24, 34,
      104, 114, 124, 134,
      204, 214, 224, 234,
    ]);
  });

  it("returns canonical source plane shapes", () => {
    const shape = { z: 3, y: 4, x: 5 };
    expect(sourcePlaneShape("z", shape)).toEqual([4, 5]);
    expect(sourcePlaneShape("y", shape)).toEqual([3, 5]);
    expect(sourcePlaneShape("x", shape)).toEqual([3, 4]);
  });
});
