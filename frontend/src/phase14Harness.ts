import { assemblePlane } from "./features/rendering/planeAssembler";
import { renderPlaneCanvas } from "./features/rendering/intensity";
import type { Axis } from "./api/viewer";
import type { ChunkLevel, DecodedChunk } from "./features/chunks/types";

function fixture(size: number): { level: ChunkLevel; chunk: DecodedChunk } {
  const depth = size >= 512 ? 1 : Math.min(16, size);
  const values = new Uint16Array(depth * size * size);
  for (let z = 0; z < depth; z += 1) {
    for (let y = 0; y < size; y += 1) {
      for (let x = 0; x < size; x += 1) {
        values[(z * size + y) * size + x] = z * 100 + y * 10 + x;
      }
    }
  }
  const level: ChunkLevel = {
    mag: "1",
    shape: [depth, size, size],
    chunks: [depth, size, size],
    grid: [1, 1, 1],
    factors: [1, 1, 1],
    dtype: "uint16",
  };
  return {
    level,
    chunk: {
      identity: {
        deployment: "browser-harness",
        volumeId: 1,
        buildIdentity: "fixture-v1",
        mag: "1",
        chunk: [0, 0, 0],
        dtype: "uint16",
        representation: "raw-le",
        authorizationScope: "test",
      },
      shape: level.shape,
      voxelOffset: [0, 0, 0],
      etag: "fixture",
      values,
      byteLength: values.byteLength,
      timing: { networkMs: 0, decodeMs: 0 },
    },
  };
}

async function render(axis: Axis, index: number, size = 32) {
  const { level, chunk } = fixture(size);
  const start = performance.now();
  const plane = assemblePlane(axis, index, level, [chunk]);
  const assembledAt = performance.now();
  const canvas = renderPlaneCanvas(plane.values, plane.shape, {
    lo: 0,
    hi: 16 * 100 + size * 11,
  });
  canvas.id = "phase14-canvas";
  canvas.style.imageRendering = "pixelated";
  document.querySelector("#phase14-canvas")?.remove();
  document.querySelector("#root")!.append(canvas);
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  const paintedAt = performance.now();
  const context = canvas.getContext("2d")!;
  return {
    shape: [canvas.height, canvas.width],
    first: [...context.getImageData(0, 0, 1, 1).data],
    last: [...context.getImageData(canvas.width - 1, canvas.height - 1, 1, 1).data],
    assemblyMs: assembledAt - start,
    visibleMs: paintedAt - start,
  };
}

async function benchmark(size: number, iterations: number) {
  const samples: number[] = [];
  for (let i = 0; i < iterations; i += 1) {
    const result = await render("z", size >= 512 ? 0 : i % Math.min(16, size), size);
    samples.push(result.visibleMs);
  }
  samples.sort((a, b) => a - b);
  const percentile = (p: number) => samples[Math.min(samples.length - 1, Math.floor(p * samples.length))];
  return {
    p50: percentile(0.5),
    p95: percentile(0.95),
    p99: percentile(0.99),
    samples,
  };
}

async function soak(iterations: number) {
  const memory = () =>
    (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory?.usedJSHeapSize ?? 0;
  await benchmark(128, 10);
  const before = memory();
  for (let i = 0; i < iterations; i += 1) {
    await render((["z", "y", "x"] as Axis[])[i % 3], i % 16, 128);
  }
  await new Promise((resolve) => setTimeout(resolve, 50));
  const after = memory();
  return { before, after, growth: before > 0 ? (after - before) / before : 0 };
}

async function comparePngAndChunk(size: number, iterations: number) {
  const seed = await render("z", 0, size);
  const seedCanvas = document.querySelector("#phase14-canvas") as HTMLCanvasElement;
  const blob = await new Promise<Blob>((resolve, reject) =>
    seedCanvas.toBlob((value) => value ? resolve(value) : reject(new Error("PNG encode failed"))),
  );
  const pngSamples: number[] = [];
  for (let i = 0; i < iterations; i += 1) {
    const started = performance.now();
    const bitmap = await createImageBitmap(blob);
    const target = document.createElement("canvas");
    target.width = bitmap.width;
    target.height = bitmap.height;
    target.getContext("2d")!.drawImage(bitmap, 0, 0);
    bitmap.close();
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    pngSamples.push(performance.now() - started);
  }
  const chunk = await benchmark(size, iterations);
  pngSamples.sort((a, b) => a - b);
  return {
    png: {
      p50: pngSamples[Math.floor(pngSamples.length * 0.5)],
      p95: pngSamples[Math.min(pngSamples.length - 1, Math.floor(pngSamples.length * 0.95))],
      encodedBytes: blob.size,
    },
    chunk,
    seedVisibleMs: seed.visibleMs,
  };
}

declare global {
  interface Window {
    phase14Harness: {
      render: typeof render;
      benchmark: typeof benchmark;
      soak: typeof soak;
      comparePngAndChunk: typeof comparePngAndChunk;
    };
  }
}
window.phase14Harness = { render, benchmark, soak, comparePngAndChunk };
