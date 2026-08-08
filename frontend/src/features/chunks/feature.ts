/**
 * Is the chunk transport built into this bundle?
 *
 * Read by `phase14ChunkRendererEnabled`, which refuses to mount a renderer over
 * a transport that is switched off. (The `createPhase13ChunkDataSource` seam
 * that used to live here was never adopted — Phase 14 mounts through
 * `ChunkRenderedImageSource` — so it had only test callers and is gone.)
 */
export function phase13ChunkLoadingEnabled(
  env: Pick<ImportMetaEnv, "VITE_FEATURE_CHUNK_PULL_QUEUE" | "VITE_MITO_UPGRADE_PROFILE"> = import.meta.env,
): boolean {
  return env.VITE_FEATURE_CHUNK_PULL_QUEUE === "true" ||
    (env.VITE_FEATURE_CHUNK_PULL_QUEUE == null &&
      ["webknossos", "production_integrated_v1"].includes(
        env.VITE_MITO_UPGRADE_PROFILE ?? "",
      ));
}
