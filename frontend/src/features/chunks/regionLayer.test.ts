import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChunkClient, ChunkTokenProvider } from "./chunkClient";
import { chunkRequestKey } from "./types";
import type { ChunkLevel, ChunkRequestIdentity } from "./types";
import { regionRgba, REGION_RGBA } from "../rendering/intensity";

/**
 * The region mask as a second streamed layer.
 *
 * What matters here is that the layer travels *with the request* — in the URL,
 * in the token, and in the cache key — so the ROI and the image can never be
 * served, cached, or authorized as each other.
 */

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

const level: ChunkLevel = {
  mag: "1",
  shape: [2, 4, 4],
  chunks: [1, 4, 4],
  grid: [2, 1, 1],
  factors: [1, 1, 1],
  dtype: "uint8",
};

function identity(layer?: "image" | "region"): ChunkRequestIdentity {
  return {
    deployment: "deploy-a",
    volumeId: 7,
    buildIdentity: "build-a",
    layer,
    mag: "1",
    chunk: [0, 0, 0],
    dtype: "uint8",
    representation: "raw-le",
    authorizationScope: "user:1",
  };
}

function chunkResponse(overrides: Record<string, string> = {}) {
  const bytes = new Uint8Array(level.shape[1] * level.shape[2]).fill(1);
  const headers = new Headers({
    "Content-Type": "application/octet-stream",
    "X-Mito-Byte-Order": "little",
    "X-Mito-Mag": "1",
    "X-Mito-Chunk": "0,0,0",
    "X-Mito-Build-Identity": "build-a",
    "X-Mito-Dtype": "uint8",
    "X-Mito-Shape": `1,${level.shape[1]},${level.shape[2]}`,
    "X-Mito-Voxel-Offset": "0,0,0",
    ETag: '"abc"',
    ...overrides,
  });
  return new Response(bytes, { status: 200, headers });
}

describe("chunk requests carry their layer", () => {
  it("asks the server for the region layer and leaves image requests unchanged", async () => {
    const urls: string[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      urls.push(String(input));
      return chunkResponse({ "X-Mito-Layer": urls.length > 1 ? "region" : "image" });
    });
    const client = new ChunkClient({ fetch: fetcher });

    await client.readAuthenticated(identity(), level);
    await client.readAuthenticated(identity("region"), level);

    expect(urls[0]).toBe("/api/volumes/7/chunks/1/0/0/0/");
    expect(urls[1]).toBe("/api/volumes/7/chunks/1/0/0/0/?layer=region");
  });

  it("rejects a response that claims a different layer", async () => {
    const client = new ChunkClient({
      fetch: async () => chunkResponse({ "X-Mito-Layer": "image" }),
    });
    await expect(client.readAuthenticated(identity("region"), level)).rejects.toThrow(
      /layer mismatch/i,
    );
  });

  it("requests capabilities per layer", async () => {
    const urls: string[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      urls.push(String(input));
      return new Response(
        JSON.stringify({
          volume_id: 7,
          build_identity: "build-a",
          layer: "region",
          layers: ["image", "region"],
          mags: [level],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    const client = new ChunkClient({ fetch: fetcher });
    const caps = await client.capabilities(7, undefined, "region");

    expect(urls[0]).toBe("/api/volumes/7/chunks/capabilities/?layer=region");
    expect(caps.layers).toEqual(["image", "region"]);
  });

  it("mints a token bound to the layers it will read", async () => {
    const bodies: string[] = [];
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      bodies.push(String(init?.body));
      return new Response(
        JSON.stringify({ token: "t", expires_at: Date.now() / 1000 + 300 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    const provider = new ChunkTokenProvider({
      volumeId: 7,
      mags: ["1"],
      layers: ["region"],
      deployment: "deploy-a",
      authorizationScope: "user:1",
      fetch: fetcher,
    });
    await provider.get();

    expect(JSON.parse(bodies[0])).toEqual({ mags: ["1"], layers: ["region"] });
    // Scope keys separate the two layers' providers, so one cannot be reused
    // for the other volume/layer combination.
    const imageProvider = new ChunkTokenProvider({
      volumeId: 7,
      mags: ["1"],
      deployment: "deploy-a",
      authorizationScope: "user:1",
      fetch: fetcher,
    });
    expect(provider.scopeKey).not.toBe(imageProvider.scopeKey);
    provider.dispose();
    imageProvider.dispose();
  });

  it("keys the cache per layer so one layer cannot serve the other", () => {
    expect(chunkRequestKey(identity("region"))).not.toBe(
      chunkRequestKey(identity("image")),
    );
    // An absent layer means image — the Phase 12 key must not change meaning.
    expect(chunkRequestKey(identity(undefined))).toBe(chunkRequestKey(identity("image")));
  });
});

describe("region planes paint as a hard-edged ROI overlay", () => {
  it("uses the same colour and alpha as the server-rendered PNG", () => {
    const rgba = regionRgba(new Uint8Array([0, 1, 0, 3]));
    expect(Array.from(rgba.slice(0, 4))).toEqual([0, 0, 0, 0]);
    expect(Array.from(rgba.slice(4, 8))).toEqual([...REGION_RGBA]);
    // Any non-zero value is "inside": a mask is categorical, not an intensity.
    expect(Array.from(rgba.slice(12, 16))).toEqual([...REGION_RGBA]);
  });
});
