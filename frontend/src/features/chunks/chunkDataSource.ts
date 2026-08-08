import { ByteLru } from "./byteLru";
import {
  ChunkClient,
  ChunkTokenProvider,
  type ChunkEndpoints,
  type ChunkTokenProviderOptions,
} from "./chunkClient";
import { Phase13Metrics } from "./metrics";
import {
  PullPriority,
  PullQueue,
  type PullHandle,
} from "./pullQueue";
import {
  chunkRequestKey,
  chunkRequestScopePrefix,
  type ChunkCapabilities,
  type ChunkDType,
  type ChunkLayer,
  type ChunkLevel,
  type ChunkRequestIdentity,
  type ChunkTypedArray,
  type DecodedChunk,
} from "./types";
import { assemblePlane } from "../rendering/planeAssembler";
import type { Axis } from "../../api/viewer";

export interface ChunkSlice {
  volumeId: number;
  buildIdentity: string;
  layer: ChunkLayer;
  mag: string;
  sourceSlice: number;
  levelSlice: number;
  shape: [number, number];
  values: ChunkTypedArray;
  byteLength: number;
  axis?: Axis;
}

export interface ScrubRequest {
  slice: number;
  generation: number;
  moving: boolean;
  direction: -1 | 0 | 1;
  prefetchRadius?: number;
}

export interface ScrubResult {
  primary: Promise<ChunkSlice>;
  refine: Promise<ChunkSlice> | null;
}

export interface ChunkDataSourceOptions {
  volumeId: number;
  deployment: string;
  authorizationScope: string;
  /** Which read-only layer this source streams. Defaults to the image. */
  layer?: ChunkLayer;
  client?: ChunkClient;
  tokenProvider?: ChunkTokenProvider;
  tokenOptions?: Omit<
    ChunkTokenProviderOptions,
    "volumeId" | "mags" | "deployment" | "authorizationScope"
  >;
  queue?: PullQueue;
  cacheBytes?: number;
  metrics?: Phase13Metrics;
  viewport?: string;
  /** Per-plane byte ceiling while scrubbing; see `selectLevels`. */
  movingPlaneBudgetBytes?: number;
  /** Where to mint tokens / read capabilities. Defaults to the authenticated
   * volume routes; a public share passes its own token-gated routes. */
  endpoints?: ChunkEndpoints;
}

function allocate(dtype: ChunkDType, length: number): ChunkTypedArray {
  switch (dtype) {
    case "uint8":
      return new Uint8Array(length);
    case "int8":
      return new Int8Array(length);
    case "uint16":
      return new Uint16Array(length);
    case "int16":
      return new Int16Array(length);
    case "uint32":
      return new Uint32Array(length);
    case "int32":
      return new Int32Array(length);
    case "float32":
      return new Float32Array(length);
    case "float64":
      return new Float64Array(length);
  }
}

function numericMag(level: ChunkLevel): number {
  const parsed = Number(level.mag);
  return Number.isFinite(parsed) ? parsed : Number.MAX_SAFE_INTEGER;
}

/**
 * How many bytes one full cross-section orthogonal to `axis` costs at `level`.
 *
 * Uses the level's own `shape`, so it accounts for a volume being anisotropic
 * or for a pyramid that does not downsample z (this one does not: every level
 * has 1-slice-deep chunks).
 */
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

function planeBytes(level: ChunkLevel, axis: Axis): number {
  const [z, y, x] = level.shape;
  const voxels = axis === "z" ? y * x : axis === "y" ? z * x : z * y;
  return voxels * DTYPE_BYTES[level.dtype];
}

/**
 * Per-plane budget while scrubbing. Sized just above a 2048² 8-bit plane
 * (4.2 MB at mag 1, 1.0 MB at mag 2) so the common crop keeps the level it
 * already used, while a 3885x4544 volume drops from a 5.2 MB mag-2 plane to a
 * 1.6 MB mag-4 one. Overridable per source for tests and tuning.
 */
const MOVING_PLANE_BUDGET_BYTES = 2 * 1024 * 1024;

/**
 * Phase 13 integration boundary: complete XY chunk-backed slice assembly,
 * intentionally not coupled to React or the existing Canvas renderer.
 */
export class ChunkDataSource {
  readonly metrics: Phase13Metrics;
  readonly queue: PullQueue;
  readonly cache: ByteLru<string, DecodedChunk>;
  readonly layer: ChunkLayer;
  /** Generation scope this source schedules under — public so a caller that
   * drives generations directly names it instead of rebuilding the string. */
  readonly viewport: string;
  private readonly client: ChunkClient;
  private readonly ownsQueue: boolean;
  private readonly handles = new Set<PullHandle<DecodedChunk>>();
  private capabilitiesValue: ChunkCapabilities | null = null;
  private tokens: ChunkTokenProvider | null;
  private ownsTokens: boolean;
  private disposed = false;

  constructor(private readonly options: ChunkDataSourceOptions) {
    if (!options.deployment || !options.authorizationScope) {
      throw new TypeError("deployment and authorizationScope are required");
    }
    this.layer = options.layer ?? "image";
    this.metrics = options.metrics ?? new Phase13Metrics();
    this.client = options.client ?? new ChunkClient({ endpoints: options.endpoints });
    this.queue =
      options.queue ??
      new PullQueue({
        maxActive: 6,
        maxActivePerVolume: 6,
        metrics: this.metrics.queue,
      });
    this.ownsQueue = options.queue === undefined;
    this.cache = new ByteLru(
      options.cacheBytes ?? 64 * 1024 * 1024,
      this.metrics.cache,
    );
    this.viewport = options.viewport ?? `volume:${options.volumeId}:${this.layer}`;
    this.tokens = options.tokenProvider ?? null;
    this.ownsTokens = !options.tokenProvider;
  }

  get capabilities(): ChunkCapabilities | null {
    return this.capabilitiesValue;
  }

  async initialize(signal?: AbortSignal): Promise<ChunkCapabilities> {
    this.assertLive();
    const next = await this.client.capabilities(
      this.options.volumeId,
      signal,
      this.layer,
    );
    const previous = this.capabilitiesValue;
    if (previous && previous.build_identity !== next.build_identity) {
      // Identity would make old entries unreachable; clearing also releases
      // their ArrayBuffers immediately instead of waiting for LRU pressure.
      this.cache.clear();
      this.tokens?.invalidate();
      const oldPrefix = chunkRequestScopePrefix(
        this.options.deployment,
        this.options.volumeId,
        previous.build_identity,
        this.layer,
      );
      for (const handle of [...this.handles]) {
        if (handle.key.startsWith(oldPrefix)) handle.cancel();
      }
    }
    this.capabilitiesValue = next;
    const expectedTokenScope = [
      this.options.deployment,
      this.options.volumeId,
      this.layer,
      next.mags.map((level) => level.mag).sort().join(","),
      this.options.authorizationScope,
    ].join("|");
    if (this.tokens && this.tokens.scopeKey !== expectedTokenScope) {
      this.capabilitiesValue = null;
      throw new Error("Chunk token provider scope does not match data source");
    }
    if (!this.tokens) {
      this.tokens = new ChunkTokenProvider({
        volumeId: this.options.volumeId,
        mags: next.mags.map((level) => level.mag),
        layers: [this.layer],
        deployment: this.options.deployment,
        authorizationScope: this.options.authorizationScope,
        endpoints: this.options.endpoints,
        onRefresh: () => this.metrics.tokenRefresh(),
        ...this.options.tokenOptions,
      });
      this.ownsTokens = true;
    }
    return next;
  }

  async refreshCapabilities(signal?: AbortSignal): Promise<ChunkCapabilities> {
    return this.initialize(signal);
  }

  scrub(request: ScrubRequest): ScrubResult {
    this.assertLive();
    const caps = this.requireCapabilities();
    this.queue.setGeneration(this.viewport, request.generation, true);
    const levels = [...caps.mags].sort((a, b) => numericMag(a) - numericMag(b));
    const finest = levels[0];
    const primaryLevel = request.moving ? levels.find((level) => numericMag(level) >= 2) ?? finest : finest;
    const primary = this.loadSlice(
      request.slice,
      primaryLevel,
      request.generation,
      PullPriority.CURRENT,
    );
    const refine =
      primaryLevel.mag !== finest.mag
        ? this.loadSlice(
            request.slice,
            finest,
            request.generation,
            PullPriority.REFINE,
          )
        : null;

    const radius = Math.max(0, Math.min(request.prefetchRadius ?? 2, 8));
    const predicted: number[] = [];
    for (let distance = 1; distance <= radius; distance += 1) {
      if (request.direction !== 0) {
        predicted.push(request.slice + request.direction * distance);
      }
    }
    if (radius > 0) {
      const opposite = request.direction === 0 ? 1 : -request.direction;
      predicted.push(request.slice + opposite);
    }
    const fullDepth = finest.shape[0] * finest.factors[0];
    for (const candidate of [...new Set(predicted)]) {
      if (candidate < 0 || candidate >= fullDepth) continue;
      void this.loadSlice(
        candidate,
        primaryLevel,
        request.generation,
        PullPriority.NEAR,
      ).catch(() => {});
    }
    return { primary, refine };
  }

  loadSlice(
    sourceSlice: number,
    level: ChunkLevel,
    generation: number,
    priority = PullPriority.CURRENT,
  ): Promise<ChunkSlice> {
    this.assertLive();
    const caps = this.requireCapabilities();
    const levelSlice = Math.floor(sourceSlice / level.factors[0]);
    if (!Number.isSafeInteger(levelSlice) || levelSlice < 0 || levelSlice >= level.shape[0]) {
      return Promise.reject(new RangeError("slice is outside the volume"));
    }
    const cz = Math.floor(levelSlice / level.chunks[0]);
    const localZ = levelSlice - cz * level.chunks[0];
    const handles: PullHandle<DecodedChunk>[] = [];
    for (let cy = 0; cy < level.grid[1]; cy += 1) {
      for (let cx = 0; cx < level.grid[2]; cx += 1) {
        handles.push(this.loadChunk(caps, level, [cz, cy, cx], generation, priority));
      }
    }
    return Promise.all(handles.map((handle) => handle.promise)).then(
      (chunks) =>
        this.assembleSlice(sourceSlice, levelSlice, localZ, level, chunks),
      (error) => {
        for (const handle of handles) handle.cancel();
        throw error;
      },
    );
  }

  /**
   * Phase 14 orthogonal plane read. Only chunks intersecting the requested
   * plane are scheduled; XZ/YZ never degrade into one request per pixel.
   */
  loadPlane(
    axis: Axis,
    sourceIndex: number,
    level: ChunkLevel,
    generation: number,
    priority = PullPriority.CURRENT,
  ): Promise<ChunkSlice> {
    this.assertLive();
    const caps = this.requireCapabilities();
    const dimension = axis === "z" ? 0 : axis === "y" ? 1 : 2;
    const levelIndex = Math.floor(sourceIndex / level.factors[dimension]);
    if (
      !Number.isSafeInteger(levelIndex) ||
      levelIndex < 0 ||
      levelIndex >= level.shape[dimension]
    ) {
      return Promise.reject(new RangeError("plane is outside the volume"));
    }
    const fixedChunk = Math.floor(levelIndex / level.chunks[dimension]);
    const handles: PullHandle<DecodedChunk>[] = [];
    for (let cz = 0; cz < level.grid[0]; cz += 1) {
      for (let cy = 0; cy < level.grid[1]; cy += 1) {
        for (let cx = 0; cx < level.grid[2]; cx += 1) {
          const coords: [number, number, number] = [cz, cy, cx];
          if (coords[dimension] !== fixedChunk) continue;
          handles.push(this.loadChunk(caps, level, coords, generation, priority));
        }
      }
    }
    return Promise.all(handles.map((handle) => handle.promise)).then(
      (chunks) => {
        const plane = assemblePlane(axis, levelIndex, level, chunks);
        return {
          volumeId: this.options.volumeId,
          buildIdentity: caps.build_identity,
          layer: this.layer,
          mag: level.mag,
          sourceSlice: sourceIndex,
          levelSlice: levelIndex,
          shape: plane.shape,
          values: plane.values,
          byteLength: plane.values.byteLength,
          axis,
        };
      },
      (error) => {
        for (const handle of handles) handle.cancel();
        throw error;
      },
    );
  }

  /**
   * Which magnification to stream now, and what to refine to afterwards.
   *
   * At rest this is always the finest level — annotation needs real pixels, and
   * that behaviour is unchanged.
   *
   * While *moving* the old rule was "anything at mag >= 2", which is a ratio,
   * not a cost: on a 3885x4544 volume mag 2 is a 5.2 MB plane, larger than the
   * 3.0 MB PNG the same scrub would have fetched from the slice endpoint, so
   * turning streaming on there made scrubbing worse rather than better. The
   * rule is now an absolute per-plane byte budget, which is the quantity that
   * actually governs how long a scrub step takes on the wire. Small volumes are
   * unaffected (a 2048² mag-2 plane is 1.0 MB, already under budget and still
   * chosen); large ones drop to the first level that fits.
   *
   * `axis` matters because the plane a scrub fetches is the cross-section
   * *orthogonal* to it, and those differ in size on an anisotropic volume.
   */
  selectLevels(
    moving: boolean,
    axis: Axis = "z",
  ): { primary: ChunkLevel; refine: ChunkLevel | null } {
    const levels = [...this.requireCapabilities().mags].sort(
      (a, b) => numericMag(a) - numericMag(b),
    );
    const finest = levels[0];
    let primary = finest;
    if (moving) {
      const budget = this.options.movingPlaneBudgetBytes ?? MOVING_PLANE_BUDGET_BYTES;
      // Finest first, so this takes the most detail that fits the budget.
      primary =
        levels.find((level) => planeBytes(level, axis) <= budget) ??
        // Nothing fits (a single huge level): fall back to the coarsest
        // available rather than the finest, which is what a budget means.
        levels[levels.length - 1];
    }
    return { primary, refine: primary.mag === finest.mag ? null : finest };
  }

  activateGeneration(generation: number, cancelOlder = true): void {
    this.assertLive();
    this.queue.setGeneration(this.viewport, generation, cancelOlder);
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const handle of [...this.handles]) handle.cancel();
    this.handles.clear();
    if (this.ownsQueue) this.queue.dispose();
    this.cache.clear();
    if (this.ownsTokens) this.tokens?.dispose();
    this.tokens = null;
    this.capabilitiesValue = null;
  }

  private loadChunk(
    caps: ChunkCapabilities,
    level: ChunkLevel,
    chunk: [number, number, number],
    generation: number,
    priority: PullPriority,
  ): PullHandle<DecodedChunk> {
    const identity: ChunkRequestIdentity = {
      deployment: this.options.deployment,
      volumeId: this.options.volumeId,
      buildIdentity: caps.build_identity,
      layer: this.layer,
      mag: level.mag,
      chunk,
      dtype: level.dtype,
      representation: "raw-le",
      authorizationScope: this.options.authorizationScope,
    };
    const key = chunkRequestKey(identity);
    const cached = this.cache.get(key);
    if (cached) {
      return {
        key,
        promise: Promise.resolve(cached),
        cancel() {},
        reprioritize() {},
      };
    }
    const tokens = this.tokens;
    if (!tokens) throw new Error("ChunkDataSource must be initialized first");
    const handle = this.queue.enqueue({
      key,
      volumeScope: `${identity.deployment}:${identity.volumeId}:${this.layer}:${identity.buildIdentity}`,
      viewport: this.viewport,
      generation,
      priority,
      run: async (signal) => {
        const result = await this.client.readSigned(identity, level, tokens, signal);
        this.metrics.chunkTiming(result.timing.networkMs, result.timing.decodeMs);
        this.cache.set(key, result);
        return result;
      },
      shouldRetry: (error) => this.client.isTransientError(error),
    });
    this.handles.add(handle);
    void handle.promise.then(
      () => this.handles.delete(handle),
      () => this.handles.delete(handle),
    );
    return handle;
  }

  private assembleSlice(
    sourceSlice: number,
    levelSlice: number,
    localZ: number,
    level: ChunkLevel,
    chunks: DecodedChunk[],
  ): ChunkSlice {
    const height = level.shape[1];
    const width = level.shape[2];
    const output = allocate(level.dtype, height * width);
    for (const chunk of chunks) {
      const [, chunkHeight, chunkWidth] = chunk.shape;
      const [, y0, x0] = chunk.voxelOffset;
      const planeStride = chunkHeight * chunkWidth;
      const sourceOffset = localZ * planeStride;
      for (let row = 0; row < chunkHeight; row += 1) {
        const sourceStart = sourceOffset + row * chunkWidth;
        const targetStart = (y0 + row) * width + x0;
        output.set(
          chunk.values.subarray(sourceStart, sourceStart + chunkWidth),
          targetStart,
        );
      }
    }
    return {
      volumeId: this.options.volumeId,
      buildIdentity: this.requireCapabilities().build_identity,
      layer: this.layer,
      mag: level.mag,
      sourceSlice,
      levelSlice,
      shape: [height, width],
      values: output,
      byteLength: output.byteLength,
    };
  }

  private requireCapabilities(): ChunkCapabilities {
    if (!this.capabilitiesValue) {
      throw new Error("ChunkDataSource must be initialized first");
    }
    return this.capabilitiesValue;
  }

  private assertLive(): void {
    if (this.disposed) throw new Error("ChunkDataSource is disposed");
  }
}
