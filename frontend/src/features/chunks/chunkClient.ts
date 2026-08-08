import { getToken } from "../../api/client";
import type {
  ChunkCapabilities,
  ChunkDType,
  ChunkLayer,
  ChunkLevel,
  ChunkRequestIdentity,
  ChunkTypedArray,
  DecodedChunk,
} from "./types";

/** `?layer=` for anything but the image, whose absence the server reads as
 * "image" — so an image request stays byte-identical to the Phase 12 one. */
function layerQuery(layer: ChunkLayer | undefined, separator = "?"): string {
  return !layer || layer === "image" ? "" : `${separator}layer=${layer}`;
}

export type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export class ChunkClientError extends Error {
  constructor(
    message: string,
    readonly code:
      | "network"
      | "unauthorized"
      | "missing"
      | "server"
      | "malformed"
      | "too_large"
      | "aborted",
    readonly status?: number,
  ) {
    super(message);
  }
}

export interface ChunkToken {
  token: string;
  expiresAt: number;
}

interface TokenResponse {
  token: string;
  expires_at: number;
}

/**
 * Where a chunk source mints tokens and reads capabilities, and what it sends
 * to identify itself there.
 *
 * Only these two calls ever needed an account: the signed chunk read is
 * anonymous and signature-verified by design. Making them injectable is what
 * lets an anonymous public-share recipient stream from the same pyramids as a
 * logged-in annotator — the share's own revocable token gate stands in for the
 * user ACL, and nothing else about the transport changes. See
 * `shareChunkEndpoints` in `api/viewer.ts` for the share implementation and
 * `PublicHardCaseChunkTokenView` on the server for why that substitution is
 * sound.
 */
export interface ChunkEndpoints {
  capabilitiesUrl(volumeId: number, layer: ChunkLayer | undefined): string;
  tokenUrl(volumeId: number): string;
  /** Sent on both calls above. Never on the signed read, which takes only the
   * minted token. */
  headers(): Headers;
}

export interface ChunkTokenProviderOptions {
  volumeId: number;
  mags: string[];
  deployment: string;
  authorizationScope: string;
  /** Layers the minted token may read. Defaults to the image layer. */
  layers?: ChunkLayer[];
  fetch?: FetchLike;
  expirySkewSeconds?: number;
  nowSeconds?: () => number;
  onRefresh?: () => void;
  /** Defaults to the authenticated volume endpoints. */
  endpoints?: ChunkEndpoints;
}

function authHeaders(): Headers {
  const headers = new Headers({ "Content-Type": "application/json" });
  const token = getToken();
  if (token) headers.set("Authorization", `Token ${token}`);
  return headers;
}

/** The authenticated volume routes — the default for every source. */
export const authedChunkEndpoints: ChunkEndpoints = {
  capabilitiesUrl: (volumeId, layer) =>
    `/api/volumes/${volumeId}/chunks/capabilities/${layerQuery(layer)}`,
  tokenUrl: (volumeId) => `/api/volumes/${volumeId}/chunks/token/`,
  headers: authHeaders,
};

async function errorFromResponse(response: Response): Promise<ChunkClientError> {
  let detail = response.statusText || `HTTP ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    // A malformed error response is still represented by its HTTP status.
  }
  const code =
    response.status === 401 || response.status === 403
      ? "unauthorized"
      : response.status === 404
        ? "missing"
        : "server";
  return new ChunkClientError(detail, code, response.status);
}

/** In-memory, scope-bound token cache with concurrent refresh collapse. */
export class ChunkTokenProvider {
  private readonly fetcher: FetchLike;
  private readonly skew: number;
  private readonly now: () => number;
  private token: ChunkToken | null = null;
  private refresh: Promise<ChunkToken> | null = null;
  private controller: AbortController | null = null;
  private disposed = false;

  constructor(private readonly options: ChunkTokenProviderOptions) {
    // Native Window.fetch requires its Window receiver in Chromium. Storing
    // it and later calling it as `this.fetcher(...)` otherwise supplies the
    // provider/client instance as `this` and throws "Illegal invocation".
    this.fetcher = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
    this.skew = options.expirySkewSeconds ?? 15;
    this.now = options.nowSeconds ?? (() => Date.now() / 1000);
    if (!options.deployment || !options.authorizationScope) {
      throw new TypeError("deployment and authorizationScope are required");
    }
  }

  get scopeKey(): string {
    // Deliberately *not* keyed on the mint route: `authorizationScope` already
    // separates an authenticated token from a share-minted one (see
    // `chunkAuthorizationScope` in AnnotationCanvas), and `ChunkDataSource`
    // reconstructs this same key to verify a caller-supplied provider — a term
    // here that it does not also add is a guaranteed mismatch.
    return [
      this.options.deployment,
      this.options.volumeId,
      [...(this.options.layers ?? ["image"])].sort().join(","),
      [...this.options.mags].sort().join(","),
      this.options.authorizationScope,
    ].join("|");
  }

  async get(signal?: AbortSignal, force = false): Promise<ChunkToken> {
    if (this.disposed) throw new ChunkClientError("token provider disposed", "aborted");
    if (
      !force &&
      this.token &&
      this.token.expiresAt - this.skew > this.now()
    ) {
      return this.token;
    }
    if (this.refresh) return this.waitForCaller(this.refresh, signal);

    this.controller = new AbortController();
    this.refresh = this.fetchToken(this.controller.signal).finally(() => {
      this.refresh = null;
      this.controller = null;
    });
    return this.waitForCaller(this.refresh, signal);
  }

  invalidate(): void {
    this.token = null;
  }

  dispose(): void {
    this.disposed = true;
    this.controller?.abort();
    this.token = null;
    this.refresh = null;
  }

  private async fetchToken(signal: AbortSignal): Promise<ChunkToken> {
    this.options.onRefresh?.();
    let response: Response;
    try {
      response = await this.fetcher(
        (this.options.endpoints ?? authedChunkEndpoints).tokenUrl(
          this.options.volumeId,
        ),
        {
          method: "POST",
          headers: (this.options.endpoints ?? authedChunkEndpoints).headers(),
          body: JSON.stringify({
            mags: this.options.mags,
            layers: this.options.layers ?? ["image"],
          }),
          signal,
          cache: "no-store",
        },
      );
    } catch (error) {
      if (signal.aborted) throw new ChunkClientError("token request aborted", "aborted");
      throw new ChunkClientError(
        error instanceof Error ? error.message : "token request failed",
        "network",
      );
    }
    if (!response.ok) throw await errorFromResponse(response);
    const payload = (await response.json()) as Partial<TokenResponse>;
    if (
      typeof payload.token !== "string" ||
      payload.token.length < 1 ||
      payload.token.length > 4096 ||
      typeof payload.expires_at !== "number" ||
      !Number.isFinite(payload.expires_at)
    ) {
      throw new ChunkClientError("Malformed chunk-token response", "malformed");
    }
    if (this.disposed) {
      throw new ChunkClientError("token provider disposed", "aborted");
    }
    this.token = { token: payload.token, expiresAt: payload.expires_at };
    return this.token;
  }

  private waitForCaller(
    shared: Promise<ChunkToken>,
    signal?: AbortSignal,
  ): Promise<ChunkToken> {
    if (!signal) return shared;
    if (signal.aborted) {
      return Promise.reject(new ChunkClientError("token request aborted", "aborted"));
    }
    return new Promise((resolve, reject) => {
      const abort = () => {
        reject(new ChunkClientError("token request aborted", "aborted"));
      };
      signal.addEventListener("abort", abort, { once: true });
      void shared.then(resolve, reject).finally(() => {
        signal.removeEventListener("abort", abort);
      });
    });
  }
}

const DTYPE_BYTES: Record<ChunkDType, number> = {
  uint8: 1,
  int8: 1,
  uint16: 2,
  int16: 2,
  uint32: 4,
  int32: 4,
  float32: 4,
  float64: 8,
};

const SUPPORTED_DTYPES = new Set<ChunkDType>(
  Object.keys(DTYPE_BYTES) as ChunkDType[],
);

function parseTuple(
  value: string | null,
  name: string,
  allowZero = false,
): [number, number, number] {
  const parts = value?.split(",").map(Number);
  if (
    !parts ||
    parts.length !== 3 ||
    parts.some(
      (part) =>
        !Number.isSafeInteger(part) || (allowZero ? part < 0 : part <= 0),
    )
  ) {
    throw new ChunkClientError(`Invalid ${name} header`, "malformed");
  }
  return parts as [number, number, number];
}

function typedArray(dtype: ChunkDType, buffer: ArrayBuffer): ChunkTypedArray {
  switch (dtype) {
    case "uint8":
      return new Uint8Array(buffer);
    case "int8":
      return new Int8Array(buffer);
    case "uint16":
      return new Uint16Array(buffer);
    case "int16":
      return new Int16Array(buffer);
    case "uint32":
      return new Uint32Array(buffer);
    case "int32":
      return new Int32Array(buffer);
    case "float32":
      return new Float32Array(buffer);
    case "float64":
      return new Float64Array(buffer);
  }
}

function transient(error: unknown): boolean {
  return (
    error instanceof ChunkClientError &&
    (error.code === "network" || (error.code === "server" && (error.status ?? 0) >= 500))
  );
}

export interface ChunkClientOptions {
  fetch?: FetchLike;
  maxResponseBytes?: number;
  now?: () => number;
  /** Defaults to the authenticated volume endpoints. */
  endpoints?: ChunkEndpoints;
}

export class ChunkClient {
  private readonly fetcher: FetchLike;
  private readonly maxResponseBytes: number;
  private readonly now: () => number;
  private readonly endpoints: ChunkEndpoints;

  constructor(options: ChunkClientOptions = {}) {
    this.fetcher = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
    this.maxResponseBytes = options.maxResponseBytes ?? 32 * 1024 * 1024;
    this.now = options.now ?? (() => performance.now());
    this.endpoints = options.endpoints ?? authedChunkEndpoints;
  }

  isTransientError(error: unknown): boolean {
    return transient(error);
  }

  async capabilities(
    volumeId: number,
    signal?: AbortSignal,
    layer?: ChunkLayer,
  ): Promise<ChunkCapabilities> {
    let response: Response;
    try {
      response = await this.fetcher(
        this.endpoints.capabilitiesUrl(volumeId, layer),
        {
          headers: this.endpoints.headers(),
          signal,
          cache: "no-store",
        },
      );
    } catch (error) {
      if (signal?.aborted) throw new ChunkClientError("request aborted", "aborted");
      throw new ChunkClientError(
        error instanceof Error ? error.message : "capabilities request failed",
        "network",
      );
    }
    if (!response.ok) throw await errorFromResponse(response);
    const payload = (await response.json()) as ChunkCapabilities;
    this.validateCapabilities(payload, volumeId);
    return payload;
  }

  async readAuthenticated(
    identity: ChunkRequestIdentity,
    level: ChunkLevel,
    signal?: AbortSignal,
  ): Promise<DecodedChunk> {
    this.validateRequest(identity, level);
    const [cz, cy, cx] = identity.chunk;
    return this.fetchAndDecode(
      `/api/volumes/${identity.volumeId}/chunks/${identity.mag}/${cz}/${cy}/${cx}/` +
        layerQuery(identity.layer),
      identity,
      level,
      authHeaders(),
      signal,
    );
  }

  async readSigned(
    identity: ChunkRequestIdentity,
    level: ChunkLevel,
    tokens: ChunkTokenProvider,
    signal?: AbortSignal,
  ): Promise<DecodedChunk> {
    this.validateRequest(identity, level);
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const token = await tokens.get(signal, attempt > 0);
      const headers = new Headers({ "X-Mito-Chunk-Token": token.token });
      const [cz, cy, cx] = identity.chunk;
      try {
        return await this.fetchAndDecode(
          `/api/chunks/signed/${identity.mag}/${cz}/${cy}/${cx}/` +
            layerQuery(identity.layer),
          identity,
          level,
          headers,
          signal,
        );
      } catch (error) {
        if (
          attempt === 0 &&
          error instanceof ChunkClientError &&
          error.code === "unauthorized"
        ) {
          tokens.invalidate();
          continue;
        }
        throw error;
      }
    }
    throw new ChunkClientError("chunk authorization failed", "unauthorized");
  }

  private async fetchAndDecode(
    path: string,
    identity: ChunkRequestIdentity,
    level: ChunkLevel,
    headers: Headers,
    signal?: AbortSignal,
  ): Promise<DecodedChunk> {
    const networkStart = this.now();
    let response: Response;
    try {
      // Phase 12 URLs do not carry build identity, and the signed route does
      // not carry volume identity. Its `immutable` response is therefore not a
      // safe browser-cache key. Phase 13's decoded LRU has the complete key;
      // bypass HTTP storage until a future API versions the URL itself.
      response = await this.fetcher(path, { headers, signal, cache: "no-store" });
    } catch (error) {
      if (signal?.aborted) throw new ChunkClientError("request aborted", "aborted");
      throw new ChunkClientError(
        error instanceof Error ? error.message : "chunk request failed",
        "network",
      );
    }
    const networkMs = this.now() - networkStart;
    if (!response.ok) throw await errorFromResponse(response);

    const lengthHeader = response.headers.get("Content-Length");
    const length = lengthHeader === null ? null : Number(lengthHeader);
    if (
      length !== null &&
      (!Number.isSafeInteger(length) || length < 0)
    ) {
      throw new ChunkClientError("Invalid Content-Length", "malformed");
    }
    if (length !== null && length > this.maxResponseBytes) {
      throw new ChunkClientError("Chunk response exceeds byte limit", "too_large");
    }
    const decodeStart = this.now();
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength > this.maxResponseBytes) {
      throw new ChunkClientError("Chunk response exceeds byte limit", "too_large");
    }
    if (length !== null && length !== buffer.byteLength) {
      throw new ChunkClientError("Content-Length does not match response", "malformed");
    }
    const decoded = this.validateAndDecode(response, buffer, identity, level);
    return {
      ...decoded,
      timing: { networkMs, decodeMs: this.now() - decodeStart },
    };
  }

  private validateAndDecode(
    response: Response,
    buffer: ArrayBuffer,
    identity: ChunkRequestIdentity,
    level: ChunkLevel,
  ): Omit<DecodedChunk, "timing"> {
    const contentType = response.headers.get("Content-Type")?.split(";")[0].trim();
    if (contentType !== "application/octet-stream") {
      throw new ChunkClientError("Chunk response is not binary", "malformed");
    }
    if (response.headers.get("X-Mito-Byte-Order") !== "little") {
      throw new ChunkClientError("Unsupported chunk byte order", "malformed");
    }
    if (response.headers.get("X-Mito-Mag") !== identity.mag) {
      throw new ChunkClientError("Chunk magnification mismatch", "malformed");
    }
    // A response that claims another layer would be composited as if it were
    // this one — a mask painted from EM intensities, or the reverse.
    const servedLayer = response.headers.get("X-Mito-Layer");
    if (servedLayer !== null && servedLayer !== (identity.layer ?? "image")) {
      throw new ChunkClientError("Chunk layer mismatch", "malformed");
    }
    if (response.headers.get("X-Mito-Chunk") !== identity.chunk.join(",")) {
      throw new ChunkClientError("Chunk address mismatch", "malformed");
    }
    if (
      response.headers.get("X-Mito-Build-Identity") !== identity.buildIdentity
    ) {
      throw new ChunkClientError("Pyramid build identity changed", "malformed");
    }
    const dtype = response.headers.get("X-Mito-Dtype") as ChunkDType | null;
    if (!dtype || !SUPPORTED_DTYPES.has(dtype) || dtype !== identity.dtype) {
      throw new ChunkClientError("Chunk dtype mismatch or unsupported dtype", "malformed");
    }
    const shape = parseTuple(response.headers.get("X-Mito-Shape"), "shape");
    const offset = parseTuple(
      response.headers.get("X-Mito-Voxel-Offset"),
      "voxel offset",
      true,
    );
    const expectedOffset = identity.chunk.map(
      (index, axis) => index * level.chunks[axis],
    );
    if (offset.some((value, axis) => value !== expectedOffset[axis])) {
      throw new ChunkClientError("Chunk voxel offset mismatch", "malformed");
    }
    for (let axis = 0; axis < 3; axis += 1) {
      const remaining = level.shape[axis] - offset[axis];
      if (shape[axis] > level.chunks[axis] || shape[axis] > remaining) {
        throw new ChunkClientError("Chunk shape exceeds advertised bounds", "malformed");
      }
    }
    const voxels = shape[0] * shape[1] * shape[2];
    const expectedBytes = voxels * DTYPE_BYTES[dtype];
    if (!Number.isSafeInteger(expectedBytes) || expectedBytes !== buffer.byteLength) {
      throw new ChunkClientError("Chunk byte count does not match shape/dtype", "malformed");
    }
    const etag = response.headers.get("ETag");
    if (!etag) {
      throw new ChunkClientError("Chunk response has no ETag", "malformed");
    }
    return {
      identity,
      shape,
      voxelOffset: offset,
      etag,
      values: typedArray(dtype, buffer),
      byteLength: buffer.byteLength,
    };
  }

  private validateCapabilities(payload: ChunkCapabilities, volumeId: number): void {
    if (
      !payload ||
      payload.volume_id !== volumeId ||
      typeof payload.build_identity !== "string" ||
      payload.build_identity.length < 1 ||
      !Array.isArray(payload.mags) ||
      payload.mags.length < 1
    ) {
      throw new ChunkClientError("Malformed chunk capabilities", "malformed");
    }
    for (const level of payload.mags) {
      if (
        typeof level.mag !== "string" ||
        !/^[1-9]\d*$/.test(level.mag) ||
        !SUPPORTED_DTYPES.has(level.dtype) ||
        !this.validTriple(level.shape, false) ||
        !this.validTriple(level.chunks, false) ||
        !this.validTriple(level.grid, false) ||
        !this.validTriple(level.factors, false) ||
        level.grid.some(
          (count, axis) => count !== Math.ceil(level.shape[axis] / level.chunks[axis]),
        )
      ) {
        throw new ChunkClientError("Malformed chunk capability level", "malformed");
      }
    }
  }

  private validateRequest(
    identity: ChunkRequestIdentity,
    level: ChunkLevel,
  ): void {
    if (
      identity.volumeId < 1 ||
      !/^[1-9]\d*$/.test(identity.mag) ||
      identity.mag !== level.mag ||
      identity.dtype !== level.dtype ||
      identity.representation !== "raw-le" ||
      !identity.deployment ||
      !identity.buildIdentity ||
      !identity.authorizationScope ||
      !this.validTriple(identity.chunk, true) ||
      identity.chunk.some((index, axis) => index >= level.grid[axis])
    ) {
      throw new ChunkClientError("Invalid chunk request identity", "malformed");
    }
  }

  private validTriple(
    value: unknown,
    allowZero: boolean,
  ): value is readonly [number, number, number] {
    return (
      Array.isArray(value) &&
      value.length === 3 &&
      value.every(
        (entry) =>
          Number.isSafeInteger(entry) && (allowZero ? entry >= 0 : entry > 0),
      )
    );
  }
}
