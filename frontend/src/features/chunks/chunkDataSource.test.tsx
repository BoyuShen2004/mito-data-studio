import { useEffect } from "react";
import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ChunkDataSource,
  type ChunkDataSourceOptions,
} from "./chunkDataSource";
import { ChunkClient, ChunkTokenProvider } from "./chunkClient";
import { phase13ChunkLoadingEnabled } from "./feature";
import { PullCancelledError, PullQueue } from "./pullQueue";
import type {
  ChunkCapabilities,
  ChunkLevel,
  ChunkRequestIdentity,
  DecodedChunk,
} from "./types";

beforeEach(() => {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: () => "test-session",
      setItem: () => {},
      removeItem: () => {},
      clear: () => {},
    },
  });
});

const fine: ChunkLevel = {
  mag: "1",
  shape: [4, 3, 3],
  chunks: [1, 2, 2],
  grid: [4, 2, 2],
  factors: [1, 1, 1],
  dtype: "uint16",
};
const coarse: ChunkLevel = {
  mag: "2",
  shape: [4, 2, 2],
  chunks: [1, 2, 2],
  grid: [4, 1, 1],
  factors: [1, 2, 2],
  dtype: "uint16",
};

function caps(build = "build-a"): ChunkCapabilities {
  return { volume_id: 7, build_identity: build, mags: [fine, coarse] };
}

class FakeClient extends ChunkClient {
  current = caps();
  reads: ChunkRequestIdentity[] = [];
  delay: Promise<void> | null = null;

  override async capabilities(): Promise<ChunkCapabilities> {
    return this.current;
  }

  override async readSigned(
    identity: ChunkRequestIdentity,
    level: ChunkLevel,
    _tokens: ChunkTokenProvider,
    signal?: AbortSignal,
  ): Promise<DecodedChunk> {
    this.reads.push(identity);
    if (this.delay) await this.delay;
    if (signal?.aborted) throw new PullCancelledError("aborted");
    const [cz, cy, cx] = identity.chunk;
    const offset: [number, number, number] = [
      cz * level.chunks[0],
      cy * level.chunks[1],
      cx * level.chunks[2],
    ];
    const shape: [number, number, number] = [
      Math.min(level.chunks[0], level.shape[0] - offset[0]),
      Math.min(level.chunks[1], level.shape[1] - offset[1]),
      Math.min(level.chunks[2], level.shape[2] - offset[2]),
    ];
    const values = new Uint16Array(shape[0] * shape[1] * shape[2]);
    let pos = 0;
    for (let z = 0; z < shape[0]; z += 1) {
      for (let y = 0; y < shape[1]; y += 1) {
        for (let x = 0; x < shape[2]; x += 1) {
          values[pos++] =
            (offset[0] + z) * 100 + (offset[1] + y) * 10 + offset[2] + x;
        }
      }
    }
    return {
      identity,
      shape,
      voxelOffset: offset,
      etag: "etag",
      values,
      byteLength: values.byteLength,
      timing: { networkMs: 1, decodeMs: 0.1 },
    };
  }
}

function sourceOptions(client: FakeClient): ChunkDataSourceOptions {
  return {
    volumeId: 7,
    deployment: "deploy-a",
    authorizationScope: "user:1",
    client,
    tokenProvider: new ChunkTokenProvider({
      volumeId: 7,
      mags: ["1", "2"],
      deployment: "deploy-a",
      authorizationScope: "user:1",
      fetch: async () => Response.json({ token: "unused", expires_at: 9999999999 }),
    }),
  };
}

describe("ChunkDataSource", () => {
  it("assembles exact XY pixels including clipped edge chunks", async () => {
    const client = new FakeClient();
    const source = new ChunkDataSource(sourceOptions(client));
    await source.initialize();
    const slice = await source.loadSlice(2, fine, 1);
    expect(slice.shape).toEqual([3, 3]);
    expect([...slice.values]).toEqual([
      200, 201, 202,
      210, 211, 212,
      220, 221, 222,
    ]);
    source.dispose();
  });

  it("loads XZ using only the intersecting Y chunk slab", async () => {
    const client = new FakeClient();
    const source = new ChunkDataSource(sourceOptions(client));
    await source.initialize();
    const plane = await source.loadPlane("y", 2, fine, 1);
    expect(plane.shape).toEqual([4, 3]);
    expect([...plane.values]).toEqual([
      20, 21, 22,
      120, 121, 122,
      220, 221, 222,
      320, 321, 322,
    ]);
    expect(client.reads).toHaveLength(8);
    expect(client.reads.every((read) => read.chunk[1] === 1)).toBe(true);
    source.dispose();
  });

  it("loads YZ using only the intersecting X chunk slab", async () => {
    const client = new FakeClient();
    const source = new ChunkDataSource(sourceOptions(client));
    await source.initialize();
    const plane = await source.loadPlane("x", 2, fine, 1);
    expect(plane.shape).toEqual([4, 3]);
    expect([...plane.values]).toEqual([
      2, 12, 22,
      102, 112, 122,
      202, 212, 222,
      302, 312, 322,
    ]);
    expect(client.reads).toHaveLength(8);
    expect(client.reads.every((read) => read.chunk[2] === 1)).toBe(true);
    source.dispose();
  });

  it("rejects invalid orthogonal slice indices without fetching", async () => {
    const client = new FakeClient();
    const source = new ChunkDataSource(sourceOptions(client));
    await source.initialize();
    await expect(source.loadPlane("y", 4, coarse, 1)).rejects.toThrow(RangeError);
    expect(client.reads).toHaveLength(0);
    source.dispose();
  });

  it("reuses decoded chunks and invalidates them after a rebuild", async () => {
    const client = new FakeClient();
    const source = new ChunkDataSource(sourceOptions(client));
    await source.initialize();
    await source.loadSlice(0, fine, 1);
    const coldReads = client.reads.length;
    await source.loadSlice(0, fine, 1);
    expect(client.reads).toHaveLength(coldReads);
    expect(source.metrics.snapshot().hits).toBeGreaterThan(0);

    client.current = caps("build-b");
    await source.refreshCapabilities();
    expect(source.cache.bytes).toBe(0);
    await source.loadSlice(0, fine, 2);
    expect(client.reads.length).toBeGreaterThan(coldReads);
    expect(client.reads[client.reads.length - 1]?.buildIdentity).toBe("build-b");
    source.dispose();
  });

  it("cancels an old-build read before clearing cache on capability refresh", async () => {
    const client = new FakeClient();
    let release!: () => void;
    client.delay = new Promise<void>((resolve) => {
      release = resolve;
    });
    const source = new ChunkDataSource(sourceOptions(client));
    await source.initialize();
    const oldRead = source.loadSlice(0, fine, 1);
    client.current = caps("build-b");
    await source.refreshCapabilities();
    release();
    await expect(oldRead).rejects.toBeInstanceOf(PullCancelledError);
    expect(source.cache.bytes).toBe(0);
    source.dispose();
  });

  it("uses coarse current data while moving and schedules fine refinement", async () => {
    const client = new FakeClient();
    const source = new ChunkDataSource(sourceOptions(client));
    await source.initialize();
    const result = source.scrub({
      slice: 1,
      generation: 1,
      moving: true,
      direction: 1,
      prefetchRadius: 0,
    });
    expect((await result.primary).mag).toBe("2");
    expect((await result.refine!).mag).toBe("1");
    source.dispose();
  });

  it("keeps the scrub plane under a byte budget on a large volume", () => {
    // The shapes here are this deployment's two real volumes. The rule this
    // pins is the reason share streaming is a win rather than a regression:
    // the old "mag >= 2" ratio would pick a 5.2 MB plane on the large volume,
    // which is *bigger* than the 3.0 MB PNG the slice endpoint would have sent.
    const levels = (mags: Array<[string, [number, number, number]]>) =>
      mags.map(([mag, shape]) => ({
        mag,
        shape,
        chunks: [1, 512, 512] as [number, number, number],
        grid: [shape[0], Math.ceil(shape[1] / 512), Math.ceil(shape[2] / 512)] as [number, number, number],
        factors: [1, Number(mag), Number(mag)] as [number, number, number],
        dtype: "uint8" as const,
      }));

    const withLevels = (mags: Array<[string, [number, number, number]]>) => {
      const client = new FakeClient();
      client.current = {
        volume_id: 7,
        build_identity: "build-a",
        layer: "image",
        mags: levels(mags),
      };
      // No `tokenProvider`: the source builds its own, so its scope key is
      // derived from these mags instead of the shared fixture's ["1","2"].
      return {
        volumeId: 7,
        deployment: "deploy-a",
        authorizationScope: "user:1",
        client,
      };
    };

    // 2048² — mag 2 is 1.0 MB, already under budget, so nothing changes.
    const small = new ChunkDataSource(withLevels([
      ["1", [256, 2048, 2048]], ["2", [128, 1024, 1024]],
      ["4", [64, 512, 512]], ["8", [32, 256, 256]],
    ]));
    // 3885x4544 — mag 2 is 5.2 MB and must be rejected in favour of mag 4.
    const large = new ChunkDataSource(withLevels([
      ["1", [160, 3885, 4544]], ["2", [80, 1943, 2272]], ["4", [40, 972, 1136]],
    ]));

    return Promise.all([small.initialize(), large.initialize()]).then(() => {
      expect(small.selectLevels(true, "z").primary.mag).toBe("2");
      expect(large.selectLevels(true, "z").primary.mag).toBe("4");
      // At rest both still deliver real pixels — annotation is unaffected.
      expect(small.selectLevels(false, "z").primary.mag).toBe("1");
      expect(large.selectLevels(false, "z").primary.mag).toBe("1");
      // …and a moving read always names the finest level as its refinement.
      expect(large.selectLevels(true, "z").refine?.mag).toBe("1");
      small.dispose();
      large.dispose();
    });
  });

  it("deduplicates repeated same-slice reads while chunks are in flight", async () => {
    const client = new FakeClient();
    let release!: () => void;
    client.delay = new Promise<void>((resolve) => {
      release = resolve;
    });
    const source = new ChunkDataSource(sourceOptions(client));
    await source.initialize();
    const one = source.loadSlice(1, fine, 1);
    const two = source.loadSlice(1, fine, 1);
    release();
    await Promise.all([one, two]);
    expect(client.reads).toHaveLength(4);
    expect(source.metrics.snapshot().deduplicated).toBe(4);
    source.dispose();
  });

  it("clips neighbor prefetch at the volume boundary", async () => {
    const client = new FakeClient();
    const source = new ChunkDataSource(sourceOptions(client));
    await source.initialize();
    await source.scrub({
      slice: 0,
      generation: 1,
      moving: false,
      direction: -1,
      prefetchRadius: 3,
    }).primary;
    await vi.waitFor(() => {
      expect(client.reads.length).toBeGreaterThan(0);
    });
    expect(client.reads.every((read) => read.chunk[0] >= 0)).toBe(true);
    expect(client.reads.every((read) => read.chunk[0] < fine.grid[0])).toBe(true);
    source.dispose();
  });

  it("latest scrub generation cancels obsolete work even if transport is slow", async () => {
    const client = new FakeClient();
    let release!: () => void;
    client.delay = new Promise<void>((resolve) => {
      release = resolve;
    });
    const source = new ChunkDataSource(sourceOptions(client));
    await source.initialize();
    const old = source.scrub({
      slice: 0,
      generation: 1,
      moving: false,
      direction: 1,
      prefetchRadius: 0,
    }).primary;
    const latest = source.scrub({
      slice: 3,
      generation: 2,
      moving: false,
      direction: 1,
      prefetchRadius: 0,
    }).primary;
    release();
    await expect(old).rejects.toBeInstanceOf(PullCancelledError);
    await expect(latest).resolves.toMatchObject({ sourceSlice: 3 });
    source.dispose();
  });

  it("isolates volume/deployment/auth scopes in request identity", async () => {
    const client = new FakeClient();
    const source = new ChunkDataSource(sourceOptions(client));
    await source.initialize();
    await source.loadSlice(0, fine, 1);
    expect(client.reads.every((read) => read.volumeId === 7)).toBe(true);
    expect(client.reads.every((read) => read.deployment === "deploy-a")).toBe(true);
    expect(client.reads.every((read) => read.authorizationScope === "user:1")).toBe(true);
    source.dispose();
  });
});

describe("Phase 13 integration seam", () => {
  it("defaults disabled and leaves the established TIFF path to its caller", () => {
    expect(
      phase13ChunkLoadingEnabled({ VITE_FEATURE_CHUNK_PULL_QUEUE: undefined }),
    ).toBe(false);
  });

  it("enables the data path through the coherent upgrade profile unless overridden", () => {
    expect(phase13ChunkLoadingEnabled({
      VITE_FEATURE_CHUNK_PULL_QUEUE: undefined,
      VITE_MITO_UPGRADE_PROFILE: "webknossos",
    })).toBe(true);
    expect(phase13ChunkLoadingEnabled({
      VITE_FEATURE_CHUNK_PULL_QUEUE: undefined,
      VITE_MITO_UPGRADE_PROFILE: "production_integrated_v1",
    })).toBe(true);
    expect(phase13ChunkLoadingEnabled({
      VITE_FEATURE_CHUNK_PULL_QUEUE: "false",
      VITE_MITO_UPGRADE_PROFILE: "webknossos",
    })).toBe(false);
  });

  it("fails closed when enabled without the Phase 11/12 backend dependencies", async () => {
    const source = new ChunkDataSource({
      volumeId: 7,
      deployment: "deploy-a",
      authorizationScope: "user:1",
      client: new ChunkClient({
        fetch: async () =>
          Response.json(
            { detail: "Chunk service disabled", reason: "disabled" },
            { status: 503 },
          ),
      }),
    });
    await expect(source.initialize()).rejects.toMatchObject({
      code: "server",
      status: 503,
    });
    source.dispose();
  });

  it("mounted consumers cancel their work without disposing a caller-owned queue", async () => {
    const client = new FakeClient();
    const queue = new PullQueue();
    const dispose = vi.spyOn(queue, "dispose");
    const options = { ...sourceOptions(client), queue };

    function Harness() {
      useEffect(() => {
        const source = new ChunkDataSource(options);
        void source.initialize().then(() => {
          void source.scrub({
            slice: 0,
            generation: 1,
            moving: false,
            direction: 0,
          }).primary.catch(() => {});
        });
        return () => source.dispose();
      }, []);
      return <div>chunk harness</div>;
    }

    const view = render(<Harness />);
    await waitFor(() => expect(client.reads.length).toBeGreaterThan(0));
    view.unmount();
    expect(dispose).not.toHaveBeenCalled();
    queue.dispose();
  });

  it("disposes its scoped queue when the adapter owns it", async () => {
    const source = new ChunkDataSource(sourceOptions(new FakeClient()));
    const dispose = vi.spyOn(source.queue, "dispose");
    await source.initialize();
    source.dispose();
    expect(dispose).toHaveBeenCalledTimes(1);
  });
});
