import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchObjectUrl,
  getRegionIndex,
  getVolumeMeta,
  imageSlicePath,
  labelSlicePath,
  regionMaskSlicePath,
  type Axis,
  type VolumeMeta,
} from "../../api/viewer";
import { useAsync } from "../../hooks/useAsync";
import CommitNumberInput from "./CommitNumberInput";
import DisplayKnobs from "./DisplayKnobs";
import JumpToRegionButton from "./JumpToRegionButton";
import { displayFilter } from "./displayAdjust";
import { panCanvasHorizontally, panCanvasVertically } from "./canvasPan";
import { useAuth } from "../../auth/AuthContext";
import { hasViewLocation, parseViewLocation, type ViewLocation } from "./viewLocation";
import {
  ChunkRenderedImageSource,
  chunkFallbackMessage,
  phase14ChunkRendererEnabled,
} from "../rendering";

// Three layers own one cache each, so 96 entries means at most 288 live blob
// URLs rather than 768. Revoking on replacement/eviction keeps long scrubs
// bounded without sacrificing the nearby-slice working set.
const MAX_CACHED_SLICES = 96;
const PREFETCH_RADIUS = 3;

export class BlobLRU {
  private map = new Map<string, string>();
  constructor(private limit: number) {}
  get(key: string) {
    const url = this.map.get(key);
    if (url) {
      this.map.delete(key);
      this.map.set(key, url);
    }
    return url;
  }
  set(key: string, url: string) {
    if (this.map.has(key)) URL.revokeObjectURL(this.map.get(key)!);
    this.map.set(key, url);
    while (this.map.size > this.limit) {
      const oldest = this.map.keys().next().value as string;
      URL.revokeObjectURL(this.map.get(oldest)!);
      this.map.delete(oldest);
    }
  }
  has(key: string) {
    return this.map.has(key);
  }
  clear() {
    for (const url of this.map.values()) URL.revokeObjectURL(url);
    this.map.clear();
  }
}

const AXES: Axis[] = ["z", "y", "x"];
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 8;
const clampZoom = (z: number) => Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, z));

export default function SliceViewer({ volumeId, onViewLocation }: { volumeId: number; onViewLocation?: (location: ViewLocation) => void }) {
  const meta = useAsync<VolumeMeta>(() => getVolumeMeta(volumeId), [volumeId]);
  const { user } = useAuth();

  const initialLocation = useRef<ViewLocation | null>(
    hasViewLocation(window.location.search) ? parseViewLocation(window.location.search) : null,
  );
  const initialAxis = initialLocation.current?.axis ?? "z";
  const [axis, setAxis] = useState<Axis>(initialAxis);
  const [index, setIndex] = useState(initialLocation.current?.[initialAxis] ?? 0);
  // Brightness/contrast are purely client-side (CSS filter on the decoded
  // image) on a 0–100 scale — 50 is neutral. Scrubbing never re-fetches.
  const [brightness, setBrightness] = useState(50);
  const [contrast, setContrast] = useState(50);
  const [showLabels, setShowLabels] = useState(true);
  const [showRegion, setShowRegion] = useState(true);
  const [roiOnly, setRoiOnly] = useState(false);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [labelUrl, setLabelUrl] = useState<string | null>(null);
  const [labelIsRegionOnly, setLabelIsRegionOnly] = useState(false);
  const [regionUrl, setRegionUrl] = useState<string | null>(null);
  const [regionOpacity, setRegionOpacity] = useState(45);
  const [zoom, setZoom] = useState(1);
  // "window" = classic fit-to-viewport (Cellable's fitWindow); "width" = fill
  // the horizontal space and let the viewport scroll vertically (fitWidth).
  const [fitMode, setFitMode] = useState<"window" | "width">("window");
  const [rendererNotice, setRendererNotice] = useState<string | null>(null);
  const [rendererRevision, setRendererRevision] = useState(0);
  const chunkRenderer = useRef<ChunkRenderedImageSource | null>(null);
  const chunkFallback = useRef(false);
  // The ROI streams on its own derivative, so it mounts, falls back, and is
  // disposed independently of the image.
  const regionRenderer = useRef<ChunkRenderedImageSource | null>(null);
  const regionFallback = useRef(false);
  const generation = useRef(0);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const axisRef = useRef(axis);
  const indexRef = useRef(index);
  axisRef.current = axis;
  indexRef.current = index;

  const cache = useRef(new BlobLRU(MAX_CACHED_SLICES));
  const labelCache = useRef(new BlobLRU(MAX_CACHED_SLICES));
  const regionCache = useRef(new BlobLRU(MAX_CACHED_SLICES));

  useEffect(() => {
    chunkRenderer.current?.dispose();
    chunkRenderer.current = null;
    chunkFallback.current = false;
    setRendererNotice(null);
    if (!phase14ChunkRendererEnabled() || !meta.data) return;
    if (meta.data.ready_streaming !== true) {
      setRendererNotice("Streaming pyramid is not ready; using the original source.");
      return;
    }
    const source = new ChunkRenderedImageSource({
      volumeId,
      deployment: window.location.origin,
      authorizationScope: `user:${user?.id ?? "authenticated"}`,
      meta: meta.data,
    });
    chunkRenderer.current = source;
    setRendererRevision((revision) => revision + 1);
    return () => {
      source.dispose();
      if (chunkRenderer.current === source) chunkRenderer.current = null;
    };
  }, [meta.data, user?.id, volumeId]);

  useEffect(() => {
    regionRenderer.current?.dispose();
    regionRenderer.current = null;
    regionFallback.current = false;
    if (
      !phase14ChunkRendererEnabled() ||
      !meta.data?.has_region_mask ||
      meta.data.region_ready_streaming !== true
    ) return;
    const source = new ChunkRenderedImageSource({
      volumeId,
      deployment: window.location.origin,
      authorizationScope: `user:${user?.id ?? "authenticated"}`,
      meta: meta.data,
      layer: "region",
    });
    regionRenderer.current = source;
    setRendererRevision((revision) => revision + 1);
    return () => {
      source.dispose();
      if (regionRenderer.current === source) regionRenderer.current = null;
    };
  }, [meta.data, user?.id, volumeId]);

  const axisLen = useMemo(() => {
    if (!meta.data) return 1;
    return meta.data.shape[axis];
  }, [meta.data, axis]);

  // Honor a deep-linked first position; later axis changes reset to the middle.
  useEffect(() => {
    if (!meta.data) return;
    const requested = initialLocation.current;
    if (requested && requested.axis === axis) {
      setIndex(Math.min(requested[axis], Math.max(meta.data.shape[axis] - 1, 0)));
      initialLocation.current = null;
    } else {
      setIndex(Math.floor(meta.data.shape[axis] / 2));
    }
  }, [meta.data, axis]);

  useEffect(() => {
    if (!meta.data || !onViewLocation) return;
    const center = {
      z: Math.floor(meta.data.shape.z / 2),
      y: Math.floor(meta.data.shape.y / 2),
      x: Math.floor(meta.data.shape.x / 2),
    };
    onViewLocation({...center, [axis]: index, axis});
  }, [axis, index, meta.data, onViewLocation]);

  const loadImage = useCallback(
    async (a: Axis, i: number, signal?: AbortSignal): Promise<string> => {
      const k = `${a}:${i}`;
      const cached = cache.current.get(k);
      if (cached) return cached;
      let url: string;
      let source = chunkFallback.current ? null : chunkRenderer.current;
      if (phase14ChunkRendererEnabled() && !chunkFallback.current && !source) {
        // Effects create the scoped source in the same commit. Yield once so
        // StrictMode's dispose/remount gap cannot issue an unintended TIFF
        // request before the replacement source is installed.
        await Promise.resolve();
        source = chunkRenderer.current;
      }
      if (source) {
        try {
          const foreground = a === axisRef.current && i === indexRef.current;
          const requestGeneration = foreground
            ? ++generation.current
            : generation.current;
          const frame = await source.render({
            axis: a,
            index: i,
            generation: requestGeneration,
            moving: foreground,
            signal,
            activateGeneration: foreground,
            onRefine: (fine) => {
              if (a !== axisRef.current || i !== indexRef.current || signal?.aborted) return;
              cache.current.set(k, fine.url);
              setImgUrl(fine.url);
            },
          });
          url = frame.url;
        } catch (error) {
          if (signal?.aborted) throw error;
          if (chunkRenderer.current !== source) throw error;
          chunkFallback.current = true;
          chunkRenderer.current?.dispose();
          chunkRenderer.current = null;
          setRendererNotice(chunkFallbackMessage(error));
          url = await fetchObjectUrl(
            imageSlicePath(volumeId, { axis: a, index: i }),
            signal,
          );
        }
      } else {
        url = await fetchObjectUrl(
          imageSlicePath(volumeId, { axis: a, index: i }),
          signal,
        );
      }
      cache.current.set(k, url);
      return url;
    },
    [volumeId],
  );

  // Region only asks the server for the filtered render (whole instances that
  // touch the ROI, nothing else) — a colorized PNG cannot be filtered by
  // instance here, and clipping it to the ROI is what cut instances in half.
  // The two variants are cached under different keys so toggling the mode
  // never serves the other one's plane.
  const regionOnlyLabels = Boolean(roiOnly && meta.data?.has_region_mask);
  const loadLabel = useCallback(
    async (a: Axis, i: number, signal?: AbortSignal): Promise<string | null> => {
      if (!meta.data?.has_label) return null;
      const k = `${a}:${i}:${regionOnlyLabels ? "roi" : "all"}`;
      const cached = labelCache.current.get(k);
      if (cached) return cached;
      try {
        const url = await fetchObjectUrl(
          labelSlicePath(volumeId, a, i, regionOnlyLabels),
          signal,
        );
        labelCache.current.set(k, url);
        return url;
      } catch {
        return null;
      }
    },
    [volumeId, meta.data, regionOnlyLabels],
  );

  const loadRegion = useCallback(
    async (a: Axis, i: number, signal?: AbortSignal): Promise<string | null> => {
      if (!meta.data?.has_region_mask) return null;
      const k = `${a}:${i}`;
      const cached = regionCache.current.get(k);
      if (cached) return cached;
      const source = regionFallback.current ? null : regionRenderer.current;
      let url: string;
      if (source) {
        try {
          const foreground = a === axisRef.current && i === indexRef.current;
          const frame = await source.render({
            axis: a,
            index: i,
            // The ROI rides the image's generation counter: both layers show
            // the same plane, so a stale ROI over a fresh image is exactly the
            // overlay drift this must not produce.
            generation: generation.current,
            moving: false,
            signal,
            activateGeneration: foreground,
          });
          url = frame.url;
        } catch (error) {
          if (signal?.aborted) throw error;
          if (regionRenderer.current !== source) throw error;
          // One-way, ROI-only fallback: the image layer keeps streaming.
          regionFallback.current = true;
          regionRenderer.current?.dispose();
          regionRenderer.current = null;
          setRendererNotice(chunkFallbackMessage(error, "region"));
          url = await fetchObjectUrl(regionMaskSlicePath(volumeId, a, i), signal);
        }
      } else {
        url = await fetchObjectUrl(regionMaskSlicePath(volumeId, a, i), signal);
      }
      regionCache.current.set(k, url);
      return url;
    },
    [volumeId, meta.data?.has_region_mask],
  );

  // Load the current slice, then prefetch neighbours so A/D scrolling is
  // smooth. Every run gets its own AbortController, cancelled the moment a
  // newer axis/index supersedes it (on the next run, or on unmount) — without
  // this, navigating quickly (fast scrubbing, or leaving the page) leaves a
  // pile of now-irrelevant multi-MB fetches in flight, starving the ones that
  // are actually still needed and making the app feel stuck.
  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    (async () => {
      try {
        let img: string | null = null;
        for (let attempt = 0; attempt < 2 && img === null; attempt += 1) {
          try {
            img = await loadImage(axis, index, controller.signal);
          } catch (error) {
            if (controller.signal.aborted || attempt > 0) throw error;
            // A source may be replaced while auth/meta settles. Retry the
            // latest viewport once against the replacement instead of
            // leaving the previously rendered axis on screen.
            await Promise.resolve();
          }
        }
        if (img === null) return;
        if (alive) setImgUrl(img);
        const [lab, region] = await Promise.all([
          showLabels ? loadLabel(axis, index, controller.signal) : null,
          showRegion || roiOnly ? loadRegion(axis, index, controller.signal) : null,
        ]);
        if (alive) {
          setLabelUrl(lab);
          // Which render `labelUrl` actually is. Until the filtered one lands,
          // the mask-image clip below still keeps labels inside the ROI.
          setLabelIsRegionOnly(regionOnlyLabels);
          setRegionUrl(region);
        }
        for (let d = 1; d <= PREFETCH_RADIUS; d++) {
          for (const n of [index + d, index - d]) {
            if (n >= 0 && n < axisLen) {
              loadImage(axis, n, controller.signal).catch(() => {});
              if (showLabels) loadLabel(axis, n, controller.signal).catch(() => {});
              if (showRegion || roiOnly) loadRegion(axis, n, controller.signal).catch(() => {});
            }
          }
        }
      } catch {
        // Superseded (aborted) or a genuine fetch failure — either way there's
        // nothing to show for this now-stale round.
      }
    })();
    return () => {
      alive = false;
      controller.abort();
    };
  }, [
    axis,
    index,
    axisLen,
    showLabels,
    showRegion,
    roiOnly,
    loadImage,
    loadLabel,
    loadRegion,
    rendererRevision,
  ]);

  // Clear caches (and free memory) when the volume changes/unmounts.
  const cacheRef = cache.current;
  const labelCacheRef = labelCache.current;
  const regionCacheRef = regionCache.current;
  useEffect(() => {
    return () => {
      cacheRef.clear();
      labelCacheRef.clear();
      regionCacheRef.clear();
    };
  }, [cacheRef, labelCacheRef, regionCacheRef]);
  useEffect(() => {
    cache.current.clear();
    labelCache.current.clear();
    regionCache.current.clear();
  }, [volumeId]);

  const step = useCallback(
    (delta: number) => setIndex((i) => Math.max(0, Math.min(axisLen - 1, i + delta))),
    [axisLen],
  );

  // A/D move through layers; arrow keys pan image position only.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (e.key === "a") {
        e.preventDefault();
        step(-1);
      } else if (e.key === "d") {
        e.preventDefault();
        step(1);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        panCanvasHorizontally(viewportRef.current, -1);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        panCanvasHorizontally(viewportRef.current, 1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        panCanvasVertically(viewportRef.current, -1);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        panCanvasVertically(viewportRef.current, 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  // Slider drags fire many onChange events; only the latest per animation
  // frame is applied, so a fast drag never queues up a backlog of state
  // updates (this — not network — was the other source of jank).
  const rafRef = useRef<number | null>(null);
  const scheduleIndex = useCallback((next: number) => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => setIndex(next));
  }, []);
  useEffect(() => () => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
  }, []);

  // Wheel over the canvas zooms (Ctrl/Cmd+scroll, the standard image-viewer
  // convention) and never touches which z-slice is showing. A plain scroll is
  // left alone entirely — not preventDefault'd — so the browser's native
  // scrolling on the viewport's overflow:auto box pans around the current
  // slice once it's zoomed in past the viewport size. Layer navigation stays
  // on A/D, the bottom layer buttons, slider, and slice-number input.
  const onWheel = useCallback((e: React.WheelEvent) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    setZoom((z) => clampZoom(z + (e.deltaY > 0 ? -0.1 : 0.1)));
  }, []);

  if (meta.loading) return <p className="muted">Loading volume…</p>;
  if (meta.error) return <div className="error">{meta.error}</div>;
  if (!meta.data) return null;

  const { shape } = meta.data;
  const filter = displayFilter(brightness, contrast);

  return (
    <div className="canvas-root">
      <div className="row spread canvas-toolrow" style={{ flexWrap: "wrap" }}>
        <div className="row" style={{ gap: "0.35rem" }}>
          <span className="muted">Plane</span>
          {AXES.map((a) => (
            <button
              key={a}
              className={a === axis ? "" : "secondary"}
              onClick={() => setAxis(a)}
            >
              {a === "z" ? "Axial (z)" : a === "y" ? "Coronal (y)" : "Sagittal (x)"}
            </button>
          ))}
        </div>
        <div className="row" style={{ gap: "0.35rem" }}>
          {meta.data.has_label && (
            <label className="row" style={{ gap: 4 }}>
              <input
                type="checkbox"
                checked={showLabels}
                onChange={(e) => setShowLabels(e.target.checked)}
              />
              <span className="muted">Labels</span>
            </label>
          )}
          {meta.data.has_region_mask && (
            <label className="row" style={{ gap: 4 }}>
              <input
                type="checkbox"
                checked={showRegion}
                onChange={(e) => setShowRegion(e.target.checked)}
              />
              <span className="muted">Region mask</span>
            </label>
          )}
        </div>
      </div>

      <div className="canvas-main-row">
        <div ref={viewportRef} onWheel={onWheel} className="canvas-viewport">
          {/* zoom=1 fits the whole slice in the viewport (no cropping) in
              "window" mode, or fills the width in "width" mode; the transform
              then scales further from that fit baseline. */}
          <div
            className="canvas-stage"
            style={{
              transform: `scale(${zoom})`,
              transformOrigin: fitMode === "window" ? "center" : "top left",
            }}
          >
            {imgUrl && (
              <img
                src={imgUrl}
                alt={`slice ${index}`}
                style={{
                  imageRendering: "pixelated",
                  filter,
                }}
              />
            )}
            {showRegion && regionUrl && (
              <img
                src={regionUrl}
                alt="immutable region mask"
                style={{
                  position: "absolute",
                  inset: 0,
                  width: "100%",
                  height: "100%",
                  imageRendering: "pixelated",
                  opacity: regionOpacity / 100,
                  pointerEvents: "none",
                }}
              />
            )}
            {showLabels && labelUrl && (!roiOnly || labelIsRegionOnly || regionUrl) && (
              <img
                src={labelUrl}
                alt="labels"
                style={{
                  position: "absolute",
                  inset: 0,
                  width: "100%",
                  height: "100%",
                  imageRendering: "pixelated",
                  pointerEvents: "none",
                  ...(roiOnly && !labelIsRegionOnly && regionUrl
                    ? {
                        maskImage: `url(${regionUrl})`,
                        WebkitMaskImage: `url(${regionUrl})`,
                        maskSize: "100% 100%",
                        WebkitMaskSize: "100% 100%",
                      }
                    : {}),
                }}
              />
            )}
          </div>
        </div>
      </div>

      <div className="canvas-controls">
        <div className="row canvas-toolrow" style={{ flexWrap: "wrap" }}>
          <button
            type="button"
            className="secondary"
            title="Previous layer (large step)"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => step(-10)}
          >
            ◀◀
          </button>
          <button
            type="button"
            className="secondary"
            title="Previous layer"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => step(-1)}
          >
            ◀
          </button>
          <input
            type="range"
            min={0}
            max={axisLen - 1}
            value={index}
            onChange={(e) => scheduleIndex(Number(e.target.value))}
            style={{ flex: 1, minWidth: 80, maxWidth: "none" }}
            title={`${axis} ${index + 1}/${axisLen}`}
          />
          <button
            type="button"
            className="secondary"
            title="Next layer"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => step(1)}
          >
            ▶
          </button>
          <button
            type="button"
            className="secondary"
            title="Next layer (large step)"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => step(10)}
          >
            ▶▶
          </button>
          <span className="muted slice-index" style={{ whiteSpace: "nowrap" }}>
            {axis}{" "}
            <CommitNumberInput
              value={index + 1}
              min={1}
              max={axisLen}
              title={`Go to ${axis} layer (1–${axisLen})`}
              widthCh={Math.max(3, String(axisLen).length)}
              onCommit={(n) => scheduleIndex(n - 1)}
            />
            /{axisLen}
          </span>
          <button className="secondary" onClick={() => setZoom((z) => clampZoom(z - 0.25))}>
            −
          </button>
          <CommitNumberInput
            value={Math.round(zoom * 100)}
            min={Math.round(MIN_ZOOM * 100)}
            max={Math.round(MAX_ZOOM * 100)}
            suffix="%"
            title="Zoom percent"
            widthCh={4}
            onCommit={(pct) => setZoom(clampZoom(pct / 100))}
          />
          <button className="secondary" onClick={() => setZoom((z) => clampZoom(z + 0.25))}>
            +
          </button>
          <JumpToRegionButton
            volumeId={volumeId}
            axis={axis}
            index={index}
            hasRegion={Boolean(meta.data.has_region_mask)}
            getRegionIndex={getRegionIndex}
            onJump={scheduleIndex}
          />
          <button
            className={fitMode === "window" ? "" : "secondary"}
            title="Fit the whole layer inside the viewport"
            onClick={() => {
              setFitMode("window");
              setZoom(1);
            }}
          >
            Fit window
          </button>
          <button
            className={fitMode === "width" ? "" : "secondary"}
            title="Fill the viewport width; scroll vertically if needed"
            onClick={() => {
              setFitMode("width");
              setZoom(1);
            }}
          >
            Fit width
          </button>
        </div>
        <DisplayKnobs
          brightness={brightness}
          contrast={contrast}
          onBrightness={setBrightness}
          onContrast={setContrast}
          regionOpacity={meta.data.has_region_mask ? regionOpacity : undefined}
          onRegionOpacity={meta.data.has_region_mask ? setRegionOpacity : undefined}
          roiOnly={meta.data.has_region_mask ? roiOnly : undefined}
          onRoiOnly={meta.data.has_region_mask ? setRoiOnly : undefined}
        />
      </div>
      <p
        className="canvas-hint"
        title={`shape z${shape.z} · y${shape.y} · x${shape.x} · Ctrl/Cmd+scroll zoom · scroll pan · A/D layer`}
      >
        z{shape.z} · y{shape.y} · x{shape.x} · Ctrl/Cmd+scroll zoom · A/D slice
      </p>
      {rendererNotice && <p className="muted" role="status">{rendererNotice}</p>}
    </div>
  );
}
