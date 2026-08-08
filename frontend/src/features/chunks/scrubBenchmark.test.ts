import { beforeAll, describe, expect, it } from "vitest";
import { ChunkClient, ChunkTokenProvider, type FetchLike } from "./chunkClient";
import { ChunkDataSource } from "./chunkDataSource";
import { PullQueue } from "./pullQueue";
import type {
  ChunkCapabilities,
  ChunkLevel,
  ChunkRequestIdentity,
} from "./types";

beforeAll(() => {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: () => "benchmark-session",
      setItem: () => {},
      removeItem: () => {},
      clear: () => {},
    },
  });
});

function quantile(values: number[], fraction: number): number {
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.min(ordered.length - 1, Math.floor(ordered.length * fraction))] ?? 0;
}

function summary(values: number[]) {
  return {
    p50: Number(quantile(values, 0.5).toFixed(3)),
    p95: Number(quantile(values, 0.95).toFixed(3)),
    p99: Number(quantile(values, 0.99).toFixed(3)),
  };
}

function benchmarkTransport(level: ChunkLevel, buildIdentity: string): FetchLike {
  return async (input, init) => {
    const url = String(input);
    if (url.includes("/chunks/capabilities/")) {
      const caps: ChunkCapabilities = {
        volume_id: 91,
        build_identity: buildIdentity,
        mags: [level],
      };
      return Response.json(caps);
    }
    if (url.includes("/chunks/token/")) {
      return Response.json({ token: "benchmark-token", expires_at: 4_000_000_000 });
    }
    const match = url.match(/\/signed\/([^/]+)\/(\d+)\/(\d+)\/(\d+)\//);
    if (!match) return Response.json({ detail: "not found" }, { status: 404 });
    if (new Headers(init?.headers).get("X-Mito-Chunk-Token") !== "benchmark-token") {
      return Response.json({ detail: "forbidden" }, { status: 403 });
    }
    const [, mag, zText, yText, xText] = match;
    const chunk = [Number(zText), Number(yText), Number(xText)] as const;
    const offset = chunk.map((index, axis) => index * level.chunks[axis]) as [
      number,
      number,
      number,
    ];
    const shape = offset.map((start, axis) =>
      Math.min(level.chunks[axis], level.shape[axis] - start),
    ) as [number, number, number];
    const bytes = new Uint8Array(shape[0] * shape[1] * shape[2] * 2);
    // Controlled same-origin transport: nonzero delay makes concurrency and
    // queue wait observable without pretending jsdom is a LAN/browser.
    await new Promise((resolve) => setTimeout(resolve, 1));
    return new Response(bytes.buffer, {
      headers: {
        "Content-Type": "application/octet-stream",
        "Content-Length": String(bytes.byteLength),
        "X-Mito-Shape": shape.join(","),
        "X-Mito-Dtype": "uint16",
        "X-Mito-Byte-Order": "little",
        "X-Mito-Mag": mag,
        "X-Mito-Chunk": chunk.join(","),
        "X-Mito-Voxel-Offset": offset.join(","),
        "X-Mito-Build-Identity": buildIdentity,
        ETag: `"${mag}-${chunk.join("-")}"`,
      },
    });
  };
}

async function runCase(plane: 512 | 2048, mag: 1 | 2 | 4) {
  const side = Math.ceil(plane / mag);
  const level: ChunkLevel = {
    mag: String(mag),
    shape: [12, side, side],
    chunks: [1, Math.min(512, side), Math.min(512, side)],
    grid: [
      12,
      Math.ceil(side / Math.min(512, side)),
      Math.ceil(side / Math.min(512, side)),
    ],
    factors: [1, mag, mag],
    dtype: "uint16",
  };
  const buildIdentity = `bench-${plane}-${mag}`;
  const transport = benchmarkTransport(level, buildIdentity);
  const client = new ChunkClient({ fetch: transport });
  const naiveTokens = new ChunkTokenProvider({
    volumeId: 91,
    mags: [String(mag)],
    deployment: "bench-deployment",
    authorizationScope: "bench-user",
    fetch: transport,
  });
  const source = new ChunkDataSource({
    volumeId: 91,
    deployment: "bench-deployment",
    authorizationScope: "bench-user",
    client,
    tokenOptions: { fetch: transport },
    cacheBytes: 160 * 1024 * 1024,
  });
  await source.initialize();

  const scrub = async (sequence: number[], generationStart: number) => {
    const latencies: number[] = [];
    let previous = sequence[0];
    for (let i = 0; i < sequence.length; i += 1) {
      const slice = sequence[i];
      const direction = Math.sign(slice - previous) as -1 | 0 | 1;
      const started = performance.now();
      await source.scrub({
        slice,
        generation: generationStart + i,
        moving: false,
        direction,
        prefetchRadius: 2,
      }).primary;
      latencies.push(performance.now() - started);
      previous = slice;
    }
    return latencies;
  };

  const cold = await scrub([0, 1, 2, 3, 4, 5, 6, 7], 1);
  // Two real consumers racing for an uncached slice exercise request collapse
  // in the measured path (not only in the scheduler unit test).
  source.queue.setGeneration(source.viewport, 15);
  await Promise.all([
    source.loadSlice(11, level, 15),
    source.loadSlice(11, level, 15),
  ]);
  const warm = await scrub([0, 1, 2, 3, 4, 5, 6, 7], 20);
  const random = await scrub([7, 1, 10, 2, 8, 0, 11, 4], 40);
  const reverse = await scrub([4, 5, 6, 5, 4, 3, 4, 5], 60);

  // Naive comparison: same strict client/transport, but one chunk at a time
  // and no queue/cache. It deliberately measures a cold plane.
  const caps = source.capabilities!;
  const naiveIdentity = (cy: number, cx: number): ChunkRequestIdentity => ({
    deployment: "bench-deployment",
    volumeId: 91,
    buildIdentity: caps.build_identity,
    mag: level.mag,
    chunk: [9, cy, cx],
    dtype: level.dtype,
    representation: "raw-le",
    authorizationScope: "bench-user",
  });
  const naiveStarted = performance.now();
  for (let cy = 0; cy < level.grid[1]; cy += 1) {
    for (let cx = 0; cx < level.grid[2]; cx += 1) {
      await client.readSigned(naiveIdentity(cy, cx), level, naiveTokens);
    }
  }
  const naiveMs = performance.now() - naiveStarted;
  const metrics = source.metrics.snapshot();
  const result = {
    plane,
    mag,
    cold: summary(cold),
    warm: summary(warm),
    random: summary(random),
    reverse: summary(reverse),
    naiveColdPlaneMs: Number(naiveMs.toFixed(3)),
    queueWait: summary(metrics.queueWaitMs),
    network: summary(metrics.networkMs),
    decode: summary(metrics.decodeMs),
    cacheHitRatio: Number(
      (metrics.hits / Math.max(1, metrics.hits + metrics.misses)).toFixed(3),
    ),
    cancellations: metrics.cancelled,
    stale: metrics.stale,
    deduplicated: metrics.deduplicated,
    tokenRefreshes: metrics.tokenRefreshes,
    retainedBytes: source.cache.bytes,
  };
  source.dispose();
  naiveTokens.dispose();
  return result;
}

async function concurrencySweep() {
  const level: ChunkLevel = {
    mag: "1",
    shape: [2, 2048, 2048],
    chunks: [1, 512, 512],
    grid: [2, 4, 4],
    factors: [1, 1, 1],
    dtype: "uint16",
  };
  const results: Record<string, number> = {};
  for (const concurrency of [1, 3, 6, 12]) {
    const transport = benchmarkTransport(level, `concurrency-${concurrency}`);
    const queue = new PullQueue({
      maxActive: concurrency,
      maxActivePerVolume: concurrency,
    });
    const source = new ChunkDataSource({
      volumeId: 91,
      deployment: "bench-deployment",
      authorizationScope: "bench-user",
      client: new ChunkClient({ fetch: transport }),
      tokenOptions: { fetch: transport },
      queue,
      cacheBytes: 32 * 1024 * 1024,
    });
    await source.initialize();
    const started = performance.now();
    await source.loadSlice(0, level, 1);
    results[String(concurrency)] = Number((performance.now() - started).toFixed(3));
    source.dispose();
    queue.dispose();
  }
  return results;
}

describe("Phase 13 realistic scrub benchmark", () => {
  it(
    "meets the controlled warm p95 gate across representative planes and mags",
    async () => {
      const results = [];
      for (const plane of [512, 2048] as const) {
        for (const mag of [1, 2, 4] as const) {
          const result = await runCase(plane, mag);
          results.push(result);
          expect(result.warm.p95).toBeLessThan(100);
          expect(result.deduplicated).toBeGreaterThan(0);
        }
      }
      const concurrency = await concurrencySweep();
      // Kept as one machine-readable line so validation can persist it without
      // scraping Vitest's presentation output.
      console.info(`PHASE13_BENCHMARK=${JSON.stringify({ results, concurrency })}`);
    },
    30_000,
  );
});
