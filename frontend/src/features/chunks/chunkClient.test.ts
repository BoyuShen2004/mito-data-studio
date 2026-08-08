import { beforeEach, describe, expect, it, vi } from "vitest";
import { setToken } from "../../api/client";
import {
  ChunkClient,
  ChunkClientError,
  ChunkTokenProvider,
  authedChunkEndpoints,
  type ChunkEndpoints,
  type FetchLike,
} from "./chunkClient";
import type { ChunkLevel, ChunkRequestIdentity } from "./types";

const level: ChunkLevel = {
  mag: "1",
  shape: [2, 3, 3],
  chunks: [1, 2, 2],
  grid: [2, 2, 2],
  factors: [1, 1, 1],
  dtype: "uint16",
};

const identity: ChunkRequestIdentity = {
  deployment: "deploy-a",
  volumeId: 7,
  buildIdentity: "build-a",
  mag: "1",
  chunk: [0, 0, 0],
  dtype: "uint16",
  representation: "raw-le",
  authorizationScope: "user:1",
};

function binaryResponse(
  values = new Uint16Array([1, 2, 3, 4]),
  overrides: Record<string, string> = {},
  status = 200,
): Response {
  const headers = new Headers({
    "Content-Type": "application/octet-stream",
    "Content-Length": String(values.byteLength),
    "X-Mito-Shape": "1,2,2",
    "X-Mito-Dtype": "uint16",
    "X-Mito-Byte-Order": "little",
    "X-Mito-Mag": "1",
    "X-Mito-Chunk": "0,0,0",
    "X-Mito-Voxel-Offset": "0,0,0",
    "X-Mito-Build-Identity": "build-a",
    ETag: '"etag"',
    ...overrides,
  });
  return new Response(values.slice().buffer, { status, headers });
}

beforeEach(() => {
  const values = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    },
  });
  setToken("session-token");
});

describe("ChunkClient", () => {
  it("decodes a valid edge chunk without padding", async () => {
    const edgeIdentity = { ...identity, chunk: [0, 1, 1] as const };
    const fetcher = vi.fn<FetchLike>(async () =>
      binaryResponse(new Uint16Array([9]), {
        "X-Mito-Shape": "1,1,1",
        "X-Mito-Chunk": "0,1,1",
        "X-Mito-Voxel-Offset": "0,2,2",
      }),
    );
    const chunk = await new ChunkClient({ fetch: fetcher }).readAuthenticated(
      edgeIdentity,
      level,
    );
    expect(chunk.shape).toEqual([1, 1, 1]);
    expect([...chunk.values]).toEqual([9]);
    expect(chunk.byteLength).toBe(2);
    const headers = new Headers(fetcher.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Token session-token");
    expect(fetcher.mock.calls[0][1]?.cache).toBe("no-store");
  });

  it.each([
    ["shape", { "X-Mito-Shape": "1,2,3" }],
    ["dtype", { "X-Mito-Dtype": "float32" }],
    ["byte order", { "X-Mito-Byte-Order": "big" }],
    ["address", { "X-Mito-Chunk": "0,0,1" }],
    ["build", { "X-Mito-Build-Identity": "old-build" }],
    ["content type", { "Content-Type": "application/json" }],
  ])("rejects malformed %s metadata", async (_name, overrides) => {
    const client = new ChunkClient({ fetch: async () => binaryResponse(undefined, overrides) });
    await expect(client.readAuthenticated(identity, level)).rejects.toBeInstanceOf(
      ChunkClientError,
    );
  });

  it("rejects responses above the configured byte budget", async () => {
    const client = new ChunkClient({
      fetch: async () => binaryResponse(),
      maxResponseBytes: 4,
    });
    await expect(client.readAuthenticated(identity, level)).rejects.toMatchObject({
      code: "too_large",
    });
  });

  it("classifies offline transport failures as retryable network errors", async () => {
    const client = new ChunkClient({
      fetch: async () => {
        throw new TypeError("offline");
      },
    });
    let failure: unknown;
    try {
      await client.readAuthenticated(identity, level);
    } catch (error) {
      failure = error;
    }
    expect(failure).toMatchObject({ code: "network" });
    expect(client.isTransientError(failure)).toBe(true);
  });

  it("validates capability build identity and levels", async () => {
    const client = new ChunkClient({
      fetch: async () =>
        Response.json({ volume_id: 7, build_identity: "build-a", mags: [level] }),
    });
    await expect(client.capabilities(7)).resolves.toMatchObject({
      build_identity: "build-a",
    });
  });

  it("rejects non-canonical magnifications before constructing a chunk URL", async () => {
    const fetcher = vi.fn(async () =>
      Response.json({
        volume_id: 7,
        build_identity: "build-a",
        mags: [{ ...level, mag: "../source" }],
      }),
    );
    await expect(
      new ChunkClient({ fetch: fetcher }).capabilities(7),
    ).rejects.toMatchObject({ code: "malformed" });
  });

  it("deduplicates concurrent token refresh and never puts a token in the URL", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      await gate;
      expect(String(input)).not.toContain("secret");
      return Response.json({ token: "secret", expires_at: 500 });
    });
    const provider = new ChunkTokenProvider({
      volumeId: 7,
      mags: ["1"],
      deployment: "deploy-a",
      authorizationScope: "user:1",
      fetch: fetcher,
      nowSeconds: () => 100,
    });
    const one = provider.get();
    const two = provider.get();
    release();
    expect(await one).toEqual(await two);
    expect(fetcher).toHaveBeenCalledTimes(1);
    provider.dispose();
  });

  it("lets one token waiter cancel without aborting the shared refresh", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const fetcher = vi.fn(async () => {
      await gate;
      return Response.json({ token: "shared", expires_at: 500 });
    });
    const provider = new ChunkTokenProvider({
      volumeId: 7,
      mags: ["1"],
      deployment: "deploy-a",
      authorizationScope: "user:1",
      fetch: fetcher,
      nowSeconds: () => 100,
    });
    const controller = new AbortController();
    const cancelled = provider.get(controller.signal);
    const survivor = provider.get();
    controller.abort();
    release();
    await expect(cancelled).rejects.toMatchObject({ code: "aborted" });
    await expect(survivor).resolves.toMatchObject({ token: "shared" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    provider.dispose();
  });

  it("refreshes once after a signed-read authorization failure", async () => {
    let tokenCount = 0;
    let signedCount = 0;
    const fetcher: FetchLike = vi.fn(async (input, init) => {
      const url = String(input);
      if (url.includes("/chunks/token/")) {
        tokenCount += 1;
        return Response.json({ token: `token-${tokenCount}`, expires_at: 500 });
      }
      signedCount += 1;
      const header = new Headers(init?.headers).get("X-Mito-Chunk-Token");
      if (header === "token-1") {
        return Response.json({ detail: "expired" }, { status: 403 });
      }
      return binaryResponse();
    });
    const provider = new ChunkTokenProvider({
      volumeId: 7,
      mags: ["1"],
      deployment: "deploy-a",
      authorizationScope: "user:1",
      fetch: fetcher,
      nowSeconds: () => 100,
    });
    const chunk = await new ChunkClient({ fetch: fetcher }).readSigned(
      identity,
      level,
      provider,
    );
    expect([...chunk.values]).toEqual([1, 2, 3, 4]);
    expect(tokenCount).toBe(2);
    expect(signedCount).toBe(2);
    provider.dispose();
  });

  it("does not retry a missing chunk or malformed response as auth", async () => {
    const fetcher: FetchLike = async (input) =>
      String(input).includes("/chunks/token/")
        ? Response.json({ token: "token", expires_at: 500 })
        : Response.json({ detail: "missing" }, { status: 404 });
    const provider = new ChunkTokenProvider({
      volumeId: 7,
      mags: ["1"],
      deployment: "deploy-a",
      authorizationScope: "user:1",
      fetch: fetcher,
      nowSeconds: () => 100,
    });
    await expect(
      new ChunkClient({ fetch: fetcher }).readSigned(identity, level, provider),
    ).rejects.toMatchObject({ code: "missing" });
    provider.dispose();
  });
});


/**
 * A public-share recipient has no account, so the two calls that used to need
 * one — mint a token, read capabilities — are the only things that have to
 * move. The signed read itself was already anonymous.
 */
describe("share-scoped chunk endpoints", () => {
  const shareEndpoints: ChunkEndpoints = {
    capabilitiesUrl: (_volumeId, layer) =>
      `/api/public/shares/tok/volumes/7/chunks/capabilities/${
        !layer || layer === "image" ? "" : `?layer=${layer}`
      }`,
    tokenUrl: () => "/api/public/shares/tok/volumes/7/chunks/token/",
    headers: () => new Headers({ "Content-Type": "application/json" }),
  };

  beforeEach(() => setToken("a-logged-in-users-token"));

  it("reads capabilities from the share route and sends no Authorization", async () => {
    const fetcher = vi.fn<FetchLike>().mockResolvedValue(
      new Response(
        JSON.stringify({
          volume_id: 7,
          build_identity: "build-a",
          layer: "image",
          mags: [level],
        }),
        { headers: { "Content-Type": "application/json" } },
      ),
    );
    const client = new ChunkClient({ fetch: fetcher, endpoints: shareEndpoints });
    await client.capabilities(7);

    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/api/public/shares/tok/volumes/7/chunks/capabilities/");
    expect((init?.headers as Headers).get("Authorization")).toBeNull();
  });

  it("mints its token from the share route, without the caller's account", async () => {
    const fetcher = vi.fn<FetchLike>().mockResolvedValue(
      new Response(
        JSON.stringify({ token: "signed", expires_at: Date.now() / 1000 + 120 }),
        { headers: { "Content-Type": "application/json" } },
      ),
    );
    const provider = new ChunkTokenProvider({
      volumeId: 7,
      mags: ["1"],
      deployment: "deploy-a",
      authorizationScope: "share:/api/public/shares/tok/volumes/7/chunks/token/",
      fetch: fetcher,
      endpoints: shareEndpoints,
    });
    const token = await provider.get();

    expect(token.token).toBe("signed");
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/api/public/shares/tok/volumes/7/chunks/token/");
    expect((init?.headers as Headers).get("Authorization")).toBeNull();
  });

  it("still uses the authenticated routes when no endpoints are supplied", () => {
    expect(authedChunkEndpoints.capabilitiesUrl(7, "region")).toBe(
      "/api/volumes/7/chunks/capabilities/?layer=region",
    );
    expect(authedChunkEndpoints.tokenUrl(7)).toBe("/api/volumes/7/chunks/token/");
    expect(authedChunkEndpoints.headers().get("Authorization")).toBe(
      "Token a-logged-in-users-token",
    );
  });
});
