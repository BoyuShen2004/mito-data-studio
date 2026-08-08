import type { Axis, VolumeMeta } from "../../api/viewer";
import { ChunkClientError, ChunkDataSource, PullPriority } from "../chunks";
import type { ChunkEndpoints } from "../chunks/chunkClient";
import { phase13ChunkLoadingEnabled } from "../chunks/feature";
import type { ChunkLayer } from "../chunks/types";
import { canvasObjectUrl, renderPlaneCanvas, renderRegionCanvas } from "./intensity";
import { sourcePlaneShape } from "./planeAssembler";

export interface RenderedFrame {
  url: string;
  mag: string;
  buildIdentity: string;
  coarse: boolean;
}

export interface RenderRequest {
  axis: Axis;
  index: number;
  generation: number;
  moving: boolean;
  signal?: AbortSignal;
  activateGeneration?: boolean;
  onRefine?: (frame: RenderedFrame) => void;
}

export interface ChunkRenderedImageSourceOptions {
  volumeId: number;
  deployment: string;
  authorizationScope: string;
  meta: VolumeMeta;
  cacheBytes?: number;
  /**
   * Which read-only layer to stream. ``"region"`` paints the ROI as the same
   * flat cyan RGBA the slice endpoint returns, so the overlay and the ROI-only
   * CSS mask behave identically whichever transport served the plane.
   */
  layer?: ChunkLayer;
  /** Token/capabilities routes. Defaults to the authenticated volume ones; a
   * public share supplies its own so an anonymous recipient can stream. */
  endpoints?: ChunkEndpoints;
}

export class ChunkRendererUnavailableError extends Error {
  constructor(
    message: string,
    readonly cause?: unknown,
  ) {
    super(message);
    this.name = "ChunkRendererUnavailableError";
  }
}

export function chunkFallbackMessage(error: unknown, layer: ChunkLayer = "image"): string {
  const cause = error instanceof ChunkRendererUnavailableError ? error.cause : error;
  const what = layer === "region" ? "The region mask is" : "";
  if (cause instanceof ChunkClientError) {
    if (cause.code === "unauthorized") {
      return "Chunk access is no longer permitted; using the TIFF/PNG source.";
    }
    if (cause.code === "missing") {
      return what
        ? `${what} not streaming yet; using the full-slice source.`
        : "The chunk pyramid is unavailable; using the TIFF/PNG source.";
    }
    if (cause.code === "malformed" || cause.code === "too_large") {
      return "Chunk data is unsupported or corrupt; using the TIFF/PNG source.";
    }
    if (cause.code === "network" || cause.code === "server") {
      return "Chunk loading is temporarily unavailable; using the TIFF/PNG source.";
    }
  }
  return "Chunk loading failed; using the TIFF/PNG source.";
}

/**
 * Adapts raw Phase 13 planes to the existing image element contract. Keeping
 * the established image and overlay elements makes the Phase 14 flag a data
 * path change rather than an annotation renderer rewrite.
 */
export class ChunkRenderedImageSource {
  private readonly source: ChunkDataSource;
  private initialized = false;
  private disposed = false;

  constructor(private readonly options: ChunkRenderedImageSourceOptions) {
    const layer = options.layer ?? "image";
    this.source = new ChunkDataSource({
      volumeId: options.volumeId,
      deployment: options.deployment,
      authorizationScope: options.authorizationScope,
      cacheBytes: options.cacheBytes,
      endpoints: options.endpoints,
      layer,
      // Separate viewports per layer: the two sources scrub together, and one
      // generation bump must not cancel the other layer's in-flight plane.
      viewport: `renderer:${options.volumeId}:${layer}`,
    });
  }

  async initialize(signal?: AbortSignal): Promise<void> {
    if (this.initialized) return;
    try {
      await this.source.initialize(signal);
      this.initialized = true;
    } catch (error) {
      throw new ChunkRendererUnavailableError("Chunk pyramid is unavailable", error);
    }
  }

  async render(request: RenderRequest): Promise<RenderedFrame> {
    await this.initialize(request.signal);
    if (request.signal?.aborted) throw new DOMException("Aborted", "AbortError");
    if (request.activateGeneration !== false) {
      this.source.activateGeneration(request.generation, true);
    }
    const levels = this.source.selectLevels(request.moving, request.axis);
    const targetShape = sourcePlaneShape(request.axis, this.options.meta.shape);
    const primary = await this.source.loadPlane(
      request.axis,
      request.index,
      levels.primary,
      request.generation,
      PullPriority.CURRENT,
    );
    const frame = await this.toFrame(primary, targetShape, levels.refine !== null);
    if (levels.refine && request.onRefine) {
      void this.source
        .loadPlane(
          request.axis,
          request.index,
          levels.refine,
          request.generation,
          PullPriority.REFINE,
        )
        .then((fine) => this.toFrame(fine, targetShape, false))
        .then((fine) => {
          if (!request.signal?.aborted && !this.disposed) request.onRefine?.(fine);
        })
        .catch(() => {
          // Coarse content remains valid. Authorization/corruption on a later
          // request is surfaced by the next foreground request and fallback.
        });
    }
    return frame;
  }

  async refreshBuild(signal?: AbortSignal): Promise<void> {
    await this.source.refreshCapabilities(signal);
  }

  inspect() {
    return {
      cacheBytes: this.source.cache.bytes,
      cacheEntries: this.source.cache.size,
      queued: this.source.queue.inspectQueue().length,
      inFlight: this.source.queue.inspectInFlight().length,
    };
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.source.dispose();
  }

  private async toFrame(
    plane: Awaited<ReturnType<ChunkDataSource["loadPlane"]>>,
    targetShape: [number, number],
    coarse: boolean,
  ): Promise<RenderedFrame> {
    const canvas =
      plane.layer === "region"
        ? renderRegionCanvas(plane.values, plane.shape, targetShape)
        : renderPlaneCanvas(
            plane.values,
            plane.shape,
            this.options.meta.display_range,
            targetShape,
          );
    const url = await canvasObjectUrl(canvas);
    return {
      url,
      mag: plane.mag,
      buildIdentity: plane.buildIdentity,
      coarse,
    };
  }
}

/**
 * The renderer mounts only when the transport beneath it is also on — the
 * documented `renderer ⇒ PullQueue` dependency, which the backend enforces in
 * the same shape for `chunk ⇒ pyramids` (deployment.E024). Without this the
 * PullQueue flag had no runtime consumer at all, so a build that turned the
 * transport off still mounted the renderer over it.
 */
export function phase14ChunkRendererEnabled(
  env: Pick<
    ImportMetaEnv,
    "VITE_FEATURE_CHUNK_RENDERER" | "VITE_FEATURE_CHUNK_PULL_QUEUE" | "VITE_MITO_UPGRADE_PROFILE"
  > = import.meta.env,
): boolean {
  const renderer =
    env.VITE_FEATURE_CHUNK_RENDERER === "true" ||
    (env.VITE_FEATURE_CHUNK_RENDERER == null &&
      ["webknossos", "production_integrated_v1"].includes(
        env.VITE_MITO_UPGRADE_PROFILE ?? "",
      ));
  return renderer && phase13ChunkLoadingEnabled(env);
}
