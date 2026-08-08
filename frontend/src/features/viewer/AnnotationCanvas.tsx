import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { Link } from "react-router-dom";
import {
  authedViewerApi,
  decodeRuns,
  deleteTrackingPrompt,
  encodeRuns,
  fetchObjectUrl,
  predictBoundary,
  predictMaskFromBox,
  predictMaskFromPoints,
  planDeleteLabel,
  getTrackingPrompts,
  putLabelIds,
  putTrackingPrompt,
  replaceTrackingPrompts,
  resetWorkingLabels,
  reviewTrackingPreview,
  runMergeLabels,
  runSplitComponents,
  runWatershed,
  setLabelLifecycle,
  trackTaskBatch,
  warmEmbedding,
  type LabelIdsResponse,
  type LabelLifecycleAction,
  type LabelSummaryRow,
  type ViewerReadApi,
  type VolumeMeta,
  type WatershedSeed,
  type TrackingPrompt,
  type TrackingPromptQueue,
  type OverwriteMode,
  type PendingToolSlice,
  type PlannedLabelSlice,
} from "../../api/viewer";
import { getDeploymentIdentity } from "../../api/deployment";
import type { HardCase } from "../../types/hardCase";
import { createHardCase } from "../../api/hardCases";
import { useAsync } from "../../hooks/useAsync";
import { useAuth } from "../../auth/AuthContext";
import {
  axisLength,
  axisShortLabel,
  DEFAULT_VIEW_AXIS,
  sliceCoordsFromVoxel,
  voxelFromSlice,
} from "./axisView";
import CommitNumberInput from "./CommitNumberInput";
import DisplayKnobs from "./DisplayKnobs";
import JumpToRegionButton from "./JumpToRegionButton";
import {
  decodeRegionMask,
  labelIdsTouchingRegion,
  protectHiddenRegionLabels,
  regionMembership,
} from "./regionOverlap";
import { OutsideRegionEditStore } from "./outsideRegionEdits";
import {
  brushRadius,
  loadBrushCursorStyle,
  saveBrushCursorStyle,
  type BrushCursorStyle,
} from "./brushCursor";
import { floodFillBlock } from "./localFloodFill";
import { planLocalInterpolationAsync } from "./localInterpolate";
import { displayFilter } from "./displayAdjust";
import { panCanvasHorizontally, panCanvasVertically } from "./canvasPan";
import { labelColor, labelColorCss } from "./labelColor";
import LabelsPanel, { type LabelsScope } from "./LabelsPanel";
import Labels3DPanel from "./Labels3DPanel";
import AnnotateToolChrome from "./annotate/AnnotateToolChrome";
import TrackRail, { type TrackingPromptTool } from "./annotate/TrackRail";
import {
  restoreTrackingPromptGeometry,
  snapshotTrackingPromptGeometry,
  type TrackingPromptGeometrySnapshot,
} from "./annotate/trackingPromptHistory";
import { nextFreshLabelId } from "./annotate/activeLabel";
import { applyInterpolateCanvasClick } from "./annotate/interpolateClick";
import { toolForShortcut } from "./annotate/shortcutKeys";
import {
  rememberLabelLayer,
  rememberedNonAdjacentPair,
  type InterpolateLayerMemory,
} from "./interpolateLayerMemory";
import {
  applyMergeCanvasClick,
  mergeClickSlotForInputs,
} from "./annotate/mergeClick";
import {
  AI_POINT_TOOLS,
  AI_PREVIEW_TOOLS,
  CONTEXT_MENU_LAYOUT,
  CONTEXT_MENU_TOOLS,
  canvasCursorForTool,
  usesCustomOverlayCursor,
  type PaintTool,
} from "./annotate/paintTools";
import { PendingSliceBuffer } from "./pendingSliceBuffer";
import { RevisionedFetch } from "./revisionedFetch";
import { SliceHistory, type CompoundSliceEdit } from "./sliceHistory";
import type { Axis } from "../../api/viewer";
import { hasViewLocation, parseViewLocation, type ViewLocation } from "./viewLocation";
import {
  ChunkRenderedImageSource,
  chunkFallbackMessage,
  phase14ChunkRendererEnabled,
} from "../rendering";

// Shared canvas for View + Annotate. Annotate-only chrome (tool strip,
// Track/SAM2) lives under `./annotate/` and mounts only when `editable`.
// Labels / 3D Labels sit on the right in both modes (resize / collapse).
//
// Tool set mirrors Cellable's left tool rail (app.py's `mode_actions` /
// canvas.py's `createMode`), laid out horizontally:
//   Select / Brush / Erase / Box Erase / Point Mask / Box Mask / Boundary / Seeds
// Track propagates the active instance across z via fork-aware SAM2.
// EfficientSAM (Point/Box/Boundary) is the interactive single-slice segmenter.

const LABEL_ALPHA = 150;
// Proposed-mask look, matching Cellable's AI preview (canvas.py paintEvent):
// green translucent fill (their `select_fill_color` + a temporary
// `label_opacity=0.5`) plus an opaque white contour (`select_line_color`,
// traced by `strokeMaskContour` above) — not the flat amber blob this used
// to be (progress/history/25-cellable-proposed-mask-fluency.md item A).
const AI_PREVIEW_FILL_ALPHA = 130; // ~0.5 of 255, same intent as Cellable's label_opacity
const AI_PREVIEW_CONTOUR_COLOR = "#ffffff";
/** Tool -> menu label, so `CONTEXT_MENU_LAYOUT` can stay a pure ordering. */
const CONTEXT_MENU_LABELS: Record<PaintTool, string> = Object.fromEntries(
  CONTEXT_MENU_TOOLS,
) as Record<PaintTool, string>;
/** Track prompt tools that *refine* the child seed rather than re-propose it —
 * switching to one of these commits a live Box/Point proposal instead of
 * throwing it away. See `changeTrackPromptTool`. */
const MANUAL_PROMPT_TOOLS: readonly TrackingPromptTool[] = ["brush", "erase", "box_erase"];
const MIN_ZOOM = 0.5; // 50%
const MAX_ZOOM = 20; // 2000%
const clampZoom = (z: number) => Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, z));
/** Wheel zoom step — milder than Cellable's 1.1^(δ/120) so trackpads feel smooth. */
const WHEEL_ZOOM_BASE = 1.045;
/** +/- button step (~8% per click). */
const BUTTON_ZOOM_FACTOR = 1.08;
const yieldToMainThread = () =>
  new Promise<void>((resolve) => window.setTimeout(resolve, 0));
/** Slice-navigation caches (see `sliceImageUrl` / `labelRunsFor`).
 * Images are blobs, so this is bounded tightly; label RLE is a few KB and can
 * afford more entries. */
const SLICE_IMG_CACHE_MAX = 16;
const SLICE_RUNS_CACHE_MAX = 64;
const SIDE_PANEL_DEFAULT = 320;
const SIDE_PANEL_MIN = 180;
const SIDE_PANEL_MAX = 520;
const SIDE_RAIL_W = 14;
const clampSidePanel = (w: number) =>
  Math.max(SIDE_PANEL_MIN, Math.min(SIDE_PANEL_MAX, Math.round(w)));

/** True-run RLE ([start, length] of contiguous truthy pixels) — the shape the
 * tracking endpoint expects for seed masks, distinct from the label-id RLE. */
function trueRunsRLE(mask: Uint8Array): [number, number][] {
  const runs: [number, number][] = [];
  let i = 0;
  while (i < mask.length) {
    if (mask[i]) {
      const start = i;
      while (i < mask.length && mask[i]) i++;
      runs.push([start, i - start]);
    } else {
      i++;
    }
  }
  return runs;
}

function maskFromTrackingSeed(runs: [number, number][], size: number): Uint8Array {
  const mask = new Uint8Array(size);
  for (const [start, length] of runs) mask.fill(1, start, Math.min(size, start + length));
  return mask;
}

function trackingPromptColor(parentId: number, childIndex: number): [number, number, number] {
  // Parent ids establish the main hue; child ids move far enough around the
  // wheel to remain distinguishable without making color carry selection.
  const hue = ((parentId * 137.508) + (childIndex - 1) * 47) % 360;
  const saturation = 0.78;
  const lightness = 0.62;
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation;
  const section = hue / 60;
  const x = chroma * (1 - Math.abs((section % 2) - 1));
  const [rr, gg, bb] = section < 1 ? [chroma, x, 0]
    : section < 2 ? [x, chroma, 0]
      : section < 3 ? [0, chroma, x]
        : section < 4 ? [0, x, chroma]
          : section < 5 ? [x, 0, chroma]
            : [chroma, 0, x];
  const m = lightness - chroma / 2;
  return [Math.round((rr + m) * 255), Math.round((gg + m) * 255), Math.round((bb + m) * 255)];
}

function compositeMaskColor(image: ImageData, mask: Uint8Array, color: [number, number, number], alpha: number) {
  const sourceAlpha = alpha / 255;
  for (let i = 0; i < mask.length; i++) {
    if (!mask[i]) continue;
    const offset = i * 4;
    const destinationAlpha = image.data[offset + 3] / 255;
    const outputAlpha = sourceAlpha + destinationAlpha * (1 - sourceAlpha);
    for (let channel = 0; channel < 3; channel++) {
      image.data[offset + channel] = Math.round(
        (color[channel] * sourceAlpha + image.data[offset + channel] * destinationAlpha * (1 - sourceAlpha)) / outputAlpha,
      );
    }
    image.data[offset + 3] = Math.round(outputAlpha * 255);
  }
}

function trackingRange(subclasses: TrackingPrompt["subclasses"]): [number, number] {
  const zs = subclasses.flatMap((child) => child.seeds.map((seed) => seed.z));
  return zs.length ? [Math.min(...zs), Math.max(...zs)] : [0, 0];
}

/** Unique nonzero instance ids present in a flat id array, sorted ascending. */
function uniqueInstances(ids: Int32Array): number[] {
  const set = new Set<number>();
  for (let i = 0; i < ids.length; i++) {
    const v = ids[i];
    if (v > 0) set.add(v);
  }
  return Array.from(set).sort((a, b) => a - b);
}

/** Cellable ports `skimage.measure.find_contours` (shape.py `_mask_outline_path`)
 * for a smooth sub-pixel iso-contour. This instead traces the exact pixel-grid
 * boundary (every edge between a mask=1 cell and a mask=0/out-of-bounds
 * neighbor) — same visual result (a crisp outline hugging the mask), avoids
 * marching-squares' saddle-point ambiguity, and is O(h*w) like the fill loop
 * it runs alongside. */
function strokeMaskContour(
  ctx: CanvasRenderingContext2D,
  mask: Uint8Array,
  h: number,
  w: number,
  lineWidth: number,
  color: string,
) {
  const at = (y: number, x: number) => (y < 0 || y >= h || x < 0 || x >= w ? 0 : mask[y * w + x]);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.beginPath();
  for (let y = 0; y < h; y++) {
    const row = y * w;
    for (let x = 0; x < w; x++) {
      if (!mask[row + x]) continue;
      if (!at(y - 1, x)) {
        ctx.moveTo(x, y);
        ctx.lineTo(x + 1, y);
      }
      if (!at(y + 1, x)) {
        ctx.moveTo(x, y + 1);
        ctx.lineTo(x + 1, y + 1);
      }
      if (!at(y, x - 1)) {
        ctx.moveTo(x, y);
        ctx.lineTo(x, y + 1);
      }
      if (!at(y, x + 1)) {
        ctx.moveTo(x + 1, y);
        ctx.lineTo(x + 1, y + 1);
      }
    }
  }
  ctx.stroke();
  ctx.restore();
}


/** High-contrast stroke for cursors on noisy EM: dark halo + bright core. */
function strokeHiVis(
  ctx: CanvasRenderingContext2D,
  lineWidth: number,
  color: string,
  path: () => void,
) {
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  const halo = lineWidth + Math.max(lineWidth * 0.9, 1.5);
  ctx.strokeStyle = "rgba(0,0,0,0.9)";
  ctx.lineWidth = halo;
  path();
  ctx.stroke();
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  path();
  ctx.stroke();
  ctx.restore();
}

/**
 * Brush/erase footprint cursor, in one of five styles.
 *
 * `cx`/`cy` are the *centre* of the hovered pixel and `radius` comes from
 * `brushRadius`, so every style outlines exactly the disc `paintAt` will
 * change — see `brushCursor.ts` for why both of those matter.
 *
 * Only `disc` (the default, unchanged in look) fills its footprint. The other
 * four exist because that fill is opaque enough to hide the membrane an
 * annotator is trying to trace on noisy EM, and each hides progressively less:
 * a thin ring, a crosshair with no ring at all, corner brackets that leave the
 * footprint's interior completely clear, and a dashed ring that reads against
 * both bright and dark tissue.
 */
function drawBrushCursor(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  radius: number,
  color: string,
  ringWidth: number,
  pipRadius: number,
  style: BrushCursorStyle,
) {
  const r = Math.max(radius, 0.5);
  const ring = () => {
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
  };
  const pip = (fill: string, size: number) => {
    ctx.beginPath();
    ctx.arc(cx, cy, Math.max(size, 0.35), 0, Math.PI * 2);
    ctx.fillStyle = fill;
    ctx.fill();
  };

  if (style === "disc") {
    ctx.save();
    ring();
    ctx.fillStyle = color === "#22c55e" ? "rgba(34,197,94,0.28)" : "rgba(56,189,248,0.28)";
    ctx.fill();
    strokeHiVis(ctx, ringWidth, color, ring);
    pip("#000", pipRadius);
    pip(color, Math.max(pipRadius * 0.55, 0.4));
    ctx.restore();
    return;
  }

  if (style === "outline") {
    // Ring only, at roughly half the default weight: the footprint stays
    // legible while the tissue inside it is completely unobscured.
    strokeHiVis(ctx, Math.max(ringWidth * 0.5, 0.6), color, ring);
    return;
  }

  if (style === "crosshair") {
    // Four short ticks that stop short of the centre, so the exact pixel being
    // painted is never covered by the cursor that aims at it.
    const gap = Math.max(r * 0.35, 0.6);
    const arm = r + Math.max(r * 0.5, 1.5);
    strokeHiVis(ctx, Math.max(ringWidth * 0.5, 0.6), color, () => {
      ctx.beginPath();
      ctx.moveTo(cx - arm, cy);
      ctx.lineTo(cx - gap, cy);
      ctx.moveTo(cx + gap, cy);
      ctx.lineTo(cx + arm, cy);
      ctx.moveTo(cx, cy - arm);
      ctx.lineTo(cx, cy - gap);
      ctx.moveTo(cx, cy + gap);
      ctx.lineTo(cx, cy + arm);
    });
    pip(color, Math.max(pipRadius * 0.45, 0.35));
    return;
  }

  if (style === "brackets") {
    // L-marks at the footprint's bounding box corners. Nothing at all is drawn
    // over the footprint itself — the least occluding of the five.
    const arm = Math.max(r * 0.45, 1);
    strokeHiVis(ctx, Math.max(ringWidth * 0.55, 0.6), color, () => {
      ctx.beginPath();
      for (const [sx, sy] of [
        [-1, -1],
        [1, -1],
        [-1, 1],
        [1, 1],
      ] as const) {
        const x = cx + sx * r;
        const y = cy + sy * r;
        ctx.moveTo(x - sx * arm, y);
        ctx.lineTo(x, y);
        ctx.lineTo(x, y - sy * arm);
      }
    });
    return;
  }

  // dashed: a broken ring. The gaps let detail through while the dashes keep
  // the outline visible over both bright and dark tissue.
  ctx.save();
  const dash = Math.max(r * 0.5, 1);
  ctx.setLineDash([dash, dash]);
  strokeHiVis(ctx, Math.max(ringWidth * 0.55, 0.6), color, ring);
  ctx.restore();
  pip(color, Math.max(pipRadius * 0.4, 0.35));
}

/** Full-frame crosshair with dark halo + bright core + center pip. */
function drawCrosshairCursor(
  ctx: CanvasRenderingContext2D,
  hx: number,
  hy: number,
  w: number,
  h: number,
  color: string,
  lineWidth: number,
  pipRadius: number,
) {
  strokeHiVis(ctx, lineWidth, color, () => {
    ctx.beginPath();
    ctx.moveTo(0, hy);
    ctx.lineTo(w, hy);
    ctx.moveTo(hx, 0);
    ctx.lineTo(hx, h);
  });
  ctx.save();
  ctx.beginPath();
  ctx.arc(hx, hy, pipRadius, 0, Math.PI * 2);
  ctx.fillStyle = "#000";
  ctx.fill();
  ctx.beginPath();
  ctx.arc(hx, hy, Math.max(pipRadius * 0.55, 0.4), 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.restore();
}

/** Compact point-prompt reticle — short arms + ring + center pip (one cursor). */
function drawPointPromptCursor(
  ctx: CanvasRenderingContext2D,
  hx: number,
  hy: number,
  color: string,
  lineWidth: number,
  armLen: number,
  ringRadius: number,
) {
  strokeHiVis(ctx, lineWidth, color, () => {
    ctx.beginPath();
    ctx.moveTo(hx - armLen, hy);
    ctx.lineTo(hx + armLen, hy);
    ctx.moveTo(hx, hy - armLen);
    ctx.lineTo(hx, hy + armLen);
    ctx.moveTo(hx + ringRadius, hy);
    ctx.arc(hx, hy, ringRadius, 0, Math.PI * 2);
  });
  ctx.save();
  ctx.beginPath();
  ctx.arc(hx, hy, Math.max(lineWidth * 0.65, 0.55), 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.restore();
}

interface AiPoint {
  x: number;
  y: number;
  label: 0 | 1;
}

interface AiPreview {
  mask: Uint8Array; // 0/1, flat h*w
  shape: [number, number];
  axis: Axis;
  index: number;
}

/** A planned interpolation, held client-side until Confirm or Cancel.
 *
 * Unlike `AiPreview` this spans *many* slices, so the masks are keyed by
 * absolute slice index and the renderer looks up whichever one the user is
 * currently scrolled to. Nothing here has been written to the volume: the
 * server recomputes the plan on Confirm, so this really is only pixels to
 * look at. Pinned to the axis it was planned on — an axis switch changes what
 * a slice index means, which would otherwise paint a preview onto an
 * unrelated plane. */
interface InterpPreview {
  axis: Axis;
  label: number;
  firstIndex: number;
  lastIndex: number;
  shape: [number, number];
  /** Absolute slice index -> 0/1 mask, flat h*w. */
  slices: Map<number, Uint8Array>;
  voxels: number;
  /** Idempotency key for confirming *this* plan. Minted per preview, not
   * derived from the request, so two deliberate interpolations of the same
   * span are two operations while a retry of one confirm stays one. */
  applyKey: string;
}

/** The interpolation preview mask for the slice currently on screen, or null.
 *
 * Guards on axis, buffer size and index so a preview planned along one axis —
 * or against a differently-shaped plane — can never be painted onto an
 * unrelated slice while the user scrolls or switches axes. Returns the same
 * `{mask}` shape `AiPreview` has, so the two proposal sources are
 * interchangeable at the render sites. */
function interpPreviewMaskFor(
  preview: InterpPreview | null,
  axis: Axis,
  index: number | null,
  size: number,
): { mask: Uint8Array } | null {
  if (!preview || index == null || preview.axis !== axis) return null;
  const mask = preview.slices.get(index);
  if (!mask || mask.length !== size) return null;
  return { mask };
}

interface BoxDrag {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

interface WsSeed {
  z: number;
  y: number;
  x: number;
  label: number;
}

/** Axis control handle published to the editor topbar (View + Annotate). */
export type AxisControls = {
  axis: Axis;
  changeAxis: (next: Axis) => void;
  disabled: boolean;
  currentLocation: () => ViewLocation;
  hasRegion: boolean;
  regionOnly: boolean;
  changeRegionOnly: (enabled: boolean) => void;
  /** How edits made outside the region are presented once Region only is
   * switched off — same policy Interpolate and Flood fill use. */
  regionOverwriteMode: OverwriteMode;
  changeRegionOverwriteMode: (mode: OverwriteMode) => void;
};

export default function AnnotationCanvas({
  taskId,
  volumeId,
  zStart,
  editable = true,
  api = authedViewerApi,
  initialActiveId,
  initialSoloId = null,
  onAxisControls,
}: {
  taskId: number;
  volumeId: number;
  zStart: number;
  zEnd: number;
  /** Annotate mounts tool strip / Track / Labels; View shares this canvas without them. */
  editable?: boolean;
  /** Read API surface — defaults to the authed task/volume endpoints; the
   * public "hard case" share page passes token-backed public endpoints so the
   * same canvas renders without an account (see `publicHardCaseApi`). */
  api?: ViewerReadApi;
  /** Initial Active instance + default solo (used by the share page to land
   * the recipient soloed on the shared label; canvas + 3D both respect it). */
  initialActiveId?: number;
  initialSoloId?: number | null;
  /** Publish axis state so the page topbar can render AxisSelect. */
  onAxisControls?: (controls: AxisControls | null) => void;
}) {
  const meta = useAsync<VolumeMeta>(() => api.getVolumeMeta(volumeId), [volumeId]);
  const labelState = useAsync(() => api.getLabelState(taskId), [taskId]);


  // View axis — Cellable Axial / Coronal / Sagittal. Default stays Axial (z).
  const [axis, setAxis] = useState<Axis>(DEFAULT_VIEW_AXIS);
  const axisRef = useRef(axis);
  axisRef.current = axis;

  const [index, setIndex] = useState(zStart);
  const indexRef = useRef(index);
  indexRef.current = index;
  // Default Annotate mode is Select (V) — not Brush/Point Mask — so opening
  // a task never starts mid-AI-prompt or mid-stroke.
  const [paintTool, setPaintTool] = useState<PaintTool>("select");
  const [brushSize, setBrushSize] = useState(6);
  const [eraserSize, setEraserSize] = useState(6);
  // Cursor look is a per-annotator preference, not per-task state, so it is
  // read from (and written back to) localStorage rather than any server row.
  const [cursorStyle, setCursorStyle] = useState<BrushCursorStyle>(loadBrushCursorStyle);
  const changeCursorStyle = useCallback((style: BrushCursorStyle) => {
    setCursorStyle(style);
    saveBrushCursorStyle(style);
  }, []);
  const [activeId, setActiveId] = useState(initialActiveId ?? 1);
  const activeIdRef = useRef(activeId);
  activeIdRef.current = activeId;
  const [brightness, setBrightness] = useState(50);
  const [contrast, setContrast] = useState(50);
  // Global committed-label opacity (0-100, Cellable's `label_opacity_slider`
  // — default 100 = fully opaque, matching Cellable's own default) — #29
  // item U5. Affects committed overlay alpha only; the AI proposal fill
  // stays at its own fixed ~0.5 regardless (#26 look, not user-tunable).
  const [labelOpacity, setLabelOpacity] = useState(100);
  const [regionOpacity, setRegionOpacity] = useState(45);
  const [roiOnly, setRoiOnly] = useState(false);
  const roiOnlyRef = useRef(roiOnly);
  roiOnlyRef.current = roiOnly;
  const [regionMaskUrl, setRegionMaskUrl] = useState<string | null>(null);
  // Region only shows whole instances, so the overlay needs the ROI as *data*
  // (which ids touch it) rather than as a mask image (which pixels are in it).
  // The decoded plane and the url it came from are kept together so a plane
  // can never be filtered by the previous plane's ROI.
  const regionMaskBitsRef = useRef<{ url: string; mask: Uint8Array } | null>(null);
  /** Who Region only currently shows (and therefore lets you edit). Volume-wide
   * — seeded from `roiVolumeIds` and grown with whatever the decoded planes and
   * unsaved paint additionally reveal. `null` means "not filtering yet". */
  const regionTouchingIdsRef = useRef<Set<number> | null>(null);
  /** The `roiVolumeIds` object `regionTouchingIdsRef` was last seeded from, so
   * a repaint can tell a fresh server answer from its own accumulated one. */
  const regionMembershipBaseRef = useRef<ReadonlySet<number> | null>(null);
  const [regionMaskBitsUrl, setRegionMaskBitsUrl] = useState<string | null>(null);
  /** Ids the server reports as touching the ROI anywhere in z. `null` = not
   * known (no ROI, not fetched yet, or the request failed), which falls back to
   * the per-plane answer rather than hiding anything it should not. */
  const [roiVolumeIds, setRoiVolumeIds] = useState<ReadonlySet<number> | null>(null);
  const roiVolumeIdsRef = useRef<ReadonlySet<number> | null>(null);
  roiVolumeIdsRef.current = roiVolumeIds;
  /** Labels list / 3D filter: hide ids that never touch the ROI anywhere.
   * Same membership set as Region only, but an independent switch — an
   * annotator often wants the list scoped to the ROI while still painting with
   * the whole volume visible. Default off (session-scoped). */
  const [hideOutsideRegion, setHideOutsideRegion] = useState(false);
  // How outside-region edits are presented once Region only is switched off —
  // the same policy Interpolate and Flood fill offer, kept separate from their
  // state so changing one never silently changes the other.
  const [regionOverwriteMode, setRegionOverwriteMode] =
    useState<OverwriteMode>("overwrite_empty");
  const regionOverwriteModeRef = useRef(regionOverwriteMode);
  regionOverwriteModeRef.current = regionOverwriteMode;
  // Paint made outside the ROI while Region only was on, per plane, with the
  // stored value it covered. See `outsideRegionEdits.ts` for why this is sparse
  // and why the raw paint buffer is never rewritten.
  const outsideEditsRef = useRef(new OutsideRegionEditStore());
  // The plane as the server last gave it to us — the "before" the record above
  // needs. Current plane only; a frozen plane keeps its baselines inside its
  // own record, so no second full plane is ever retained per pending slice.
  const baselineIdsRef = useRef<Int32Array | null>(null);
  // Bumped when an outside edit is recorded, so the overlay repaints.
  const [outsideEditRevision, setOutsideEditRevision] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [fitMode, setFitMode] = useState<"window" | "width">("window");
  const [status, setStatus] = useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle");
  const [sliceLoading, setSliceLoading] = useState(true);
  // Every slice the user has edited but not yet saved. Navigating z freezes
  // only the actually edited slice here and restores it on return; clean
  // slices must never enter this buffer (one full raster each).
  const { user: authUser } = useAuth();
  /** Tool shortcuts from this account's profile — always complete (the server
   * fills unset tools from the defaults), so there is no local fallback map. */
  const annotateShortcuts = authUser?.annotate_shortcuts ?? null;
  // Names the credential a chunk token is cached under. For an authenticated
  // viewer that is the user; for a share it is the share's own mint route,
  // which is per-token — so two recipients of two different shares of the same
  // volume never read each other's cached token, and neither reads the
  // logged-in user's.
  const chunkAuthorizationScope = useMemo(
    () =>
      api.chunkEndpoints && api !== authedViewerApi
        ? `share:${api.chunkEndpoints.tokenUrl(volumeId)}`
        : `user:${authUser?.id ?? "authenticated"}`,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [authUser?.id, volumeId],
  );
  const chunkRendererRef = useRef<ChunkRenderedImageSource | null>(null);
  const chunkGenerationRef = useRef(0);
  const lastChunkNavigationRef = useRef<{ axis: Axis; index: number; at: number } | null>(null);
  const chunkFallbackRef = useRef(false);
  // The ROI is a second read-only derivative: it mounts, streams and falls back
  // on its own, and never touches the label write path.
  const regionRendererRef = useRef<ChunkRenderedImageSource | null>(null);
  const regionFallbackRef = useRef(false);
  const [rendererNotice, setRendererNotice] = useState<string | null>(null);
  const [chunkRendererRevision, setChunkRendererRevision] = useState(0);
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);
  const pendingSlicesRef = useRef(new PendingSliceBuffer());
  // `dirtyRef` means at least one slice is dirty. This narrower flag prevents
  // that global state from making every clean slice visited afterwards look
  // dirty (and retaining a full Int32Array for each one).
  const currentSliceDirtyRef = useRef(false);
  const saveInFlightRef = useRef<Promise<boolean> | null>(null);
  // Which z `idsRef` currently belongs to. `index` can advance before the
  // new slice finishes loading — without this, paint/Save can stash the
  // previous slice's buffer under the new z (cross-slice overwrite).
  const idsIndexRef = useRef<number | null>(null);
  const [instances, setInstances] = useState<number[]>([]);
  const [hiddenIds, setHiddenIds] = useState<Set<number>>(new Set());
  const [soloId, setSoloId] = useState<number | null>(initialSoloId);
  const [undoCount, setUndoCount] = useState(0);
  const [redoCount, setRedoCount] = useState(0);

  // Point Mask / Box Mask / Boundary — accumulated prompt points + the
  // predicted preview mask, awaiting an explicit Commit (so a bad/slow
  // prediction never silently flattens into the raster before the user
  // sees it — Cellable's Shape stayed editable/undo-able until then too).
  const [aiError, setAiError] = useState<string | null>(null);
  const [hasAiPreview, setHasAiPreview] = useState(false);
  const [aiPointCount, setAiPointCount] = useState(0);

  // Seeds (3D watershed) — persists across slice navigation on purpose
  // (seeds can span several z's before "Run Watershed", like Cellable's
  // watershed_3d mode).
  const [wsSeeds, setWsSeeds] = useState<WsSeed[]>([]);
  const [wsTargetLabel, setWsTargetLabel] = useState<number | null>(null);
  const [wsRunning, setWsRunning] = useState(false);

  // Interpolate (WK-style SDF blend, ADR-006) — the two endpoint slices the
  // active label is already painted on, plus the previewed intermediates. The
  // preview lives in a ref (it is per-slice pixel data the renderer reads, not
  // something React should diff); `interpPreviewCount` is the state mirror the
  // chrome needs to switch between "Preview" and "Confirm/Cancel".
  const [interpFirst, setInterpFirst] = useState<number | null>(null);
  const [interpLast, setInterpLast] = useState<number | null>(null);
  const interpLayerMemoryRef = useRef<InterpolateLayerMemory>(new Map());
  /** First slice click while waiting for the matching second endpoint. */
  const interpAnchorRef = useRef<{ label: number; layer: number } | null>(null);
  const interpContextSelectionRef = useRef(false);
  const [interpRunning, setInterpRunning] = useState(false);
  const [interpPreviewCount, setInterpPreviewCount] = useState(0);
  const [overwriteMode, setOverwriteMode] = useState<OverwriteMode>("overwrite_empty");
  const [trackOverwriteMode, setTrackOverwriteMode] = useState<OverwriteMode>("overwrite_empty");
  const [floodFillEnabled, setFloodFillEnabled] = useState(false);
  const [floodDepth, setFloodDepth] = useState(1);
  const [floodRunning, setFloodRunning] = useState(false);
  const interpPreviewRef = useRef<InterpPreview | null>(null);
  // Asked once per session (the identity endpoint is cached): applying an
  // interpolation records an annotation operation, so the tool needs *both*
  // flags — offering it with only FEATURE_INTERPOLATION on would give a
  // preview that can never be confirmed.
  const [interpolationEnabled, setInterpolationEnabled] = useState(false);
  useEffect(() => {
    let alive = true;
    getDeploymentIdentity().then((identity) => {
      if (!alive) return;
      const flags = identity?.features ?? {};
      setInterpolationEnabled(
        Boolean(flags.FEATURE_INTERPOLATION && flags.FEATURE_ANNOTATION_OPS),
      );
      setFloodFillEnabled(
        Boolean(flags.FEATURE_ANNOTATION_TOOLS && flags.FEATURE_ANNOTATION_OPS),
      );
    });
    return () => {
      alive = false;
    };
  }, []);

  // Position parameters refine an already-authorized route/public-share URL.
  useEffect(() => {
    if (!hasViewLocation(window.location.search)) return;
    const location = parseViewLocation(window.location.search);
    setAxis(location.axis);
    setIndex(location[location.axis]);
    if (location.label) setActiveId(location.label);
  }, []);

  // Split 3D (connected components) — click a label or Split Active.
  const [splitRunning, setSplitRunning] = useState(false);

  // Merge — two label ids; voxels of the larger id become the smaller id.
  const [mergeIdA, setMergeIdA] = useState<number | null>(null);
  const [mergeIdB, setMergeIdB] = useState<number | null>(null);
  const [mergeRunning, setMergeRunning] = useState(false);
  /** Alternates 0/1 — which merge input the next canvas click fills. */
  const mergeClickSlotRef = useRef<0 | 1>(0);
  const [deleteRunning, setDeleteRunning] = useState(false);
  /** Reset-to-registered is a whole-volume server write, so it disables its own
   * control while it runs rather than the whole toolbar. */
  const [resetRunning, setResetRunning] = useState(false);

  // Track (SAM2) has its own prompt-only canvas. Parent/child prompt masks
  // remain separate from permanent annotation ids until propagation merges
  // every child back into its parent class.
  const [tracking, setTracking] = useState(false);
  const [trackingParentIds, setTrackingParentIds] = useState<number[]>([]);
  const [trackError, setTrackError] = useState<string | null>(null);
  const [trackingPrompts, setTrackingPrompts] = useState<TrackingPrompt[]>([]);
  const [selectedTrackParent, setSelectedTrackParent] = useState<number | null>(null);
  const [selectedTrackSubclass, setSelectedTrackSubclass] = useState<number | null>(null);
  /** `local` marks a preview that only exists in this browser's pending buffer
   * — the batch endpoint plans rather than writes, so there is nothing on the
   * server to Confirm or Reject. Server-side previews (legacy volumes that
   * were propagated through the publishing path) still arrive without it. */
  const [trackingPendingReview, setTrackingPendingReview] = useState<{ parent_ids: number[]; status: "pending_review"; local?: boolean } | null>(null);
  const [trackReviewAction, setTrackReviewAction] = useState<"confirm" | "reject" | null>(null);
  const [trackUndoCount, setTrackUndoCount] = useState(0);
  const [trackRedoCount, setTrackRedoCount] = useState(0);
  const [trackPromptHistoryBusy, setTrackPromptHistoryBusy] = useState(false);
  const [trackPromptTool, setTrackPromptTool] = useState<TrackingPromptTool | null>(null);
  const [trackProgressSaving, setTrackProgressSaving] = useState(false);
  const [trackProgressSaved, setTrackProgressSaved] = useState(false);
  const [trackPromptBrushSize, setTrackPromptBrushSize] = useState(8);
  const [trackPromptEraserSize, setTrackPromptEraserSize] = useState(12);
  const [trackPromptRevision, setTrackPromptRevision] = useState(0);

  const resolveTrackingPendingReview = useCallback((queue: TrackingPromptQueue) => {
    if (queue.pending_review?.parent_ids?.length) return queue.pending_review;
    const parentIds = queue.items
      .filter((item) => item.status === "pending")
      .map((item) => item.parent_id);
    return parentIds.length ? { parent_ids: parentIds, status: "pending_review" as const } : null;
  }, []);

  const syncTrackingQueue = useCallback(async () => {
    const queue = await getTrackingPrompts(taskId);
    setTrackingPrompts(queue.items);
    setTrackingPendingReview(resolveTrackingPendingReview(queue));
    return queue;
  }, [resolveTrackingPendingReview, taskId]);

  useEffect(() => {
    if (!editable) return;
    let live = true;
    getTrackingPrompts(taskId)
      .then((queue) => {
        if (!live) return;
        trackUndoRef.current = [];
        trackRedoRef.current = [];
        setTrackUndoCount(0);
        setTrackRedoCount(0);
        setTrackingPrompts(queue.items);
        setTrackingPendingReview(resolveTrackingPendingReview(queue));
        if (queue.items.length) {
          setSelectedTrackParent((current) => current ?? queue.items[0].parent_id);
          setSelectedTrackSubclass((current) => current ?? queue.items[0].subclasses[0]?.index ?? null);
        }
      })
      .catch((e) => live && setTrackError(e instanceof Error ? e.message : "Could not load Track prompts"));
    return () => { live = false; };
  }, [editable, resolveTrackingPendingReview, taskId]);

  // Minimal right-click context menu (#29 item U15) — screen position to
  // place it at, plus the label id under the cursor (if any) so Verify/Solo
  // only show up when right-clicking an actual label.
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; labelId: number | null } | null>(null);

  // Swap 3D ↔ Canvas (#31 item 5) — swapped = the 3D Labels view fills the
  // large center pane and the 2D canvas shrinks into the small dock slot,
  // view-only (no paint/AI/box/seed — see `onPointerDown`'s `swapped` guard
  // below). `pinned3D`/`activeId` (and therefore `label3DIds`) are untouched
  // by this toggle, so the 3D selection survives swapping either direction.
  const [swapped, setSwapped] = useState(false);
  // Excel-style side docks: drag to resize; collapse to a thin reopen strip.
  const [leftPanelW, setLeftPanelW] = useState(SIDE_PANEL_DEFAULT);
  const [rightPanelW, setRightPanelW] = useState(SIDE_PANEL_DEFAULT);
  const [leftPanelOpen, setLeftPanelOpen] = useState(true);
  const [rightPanelOpen, setRightPanelOpen] = useState(true);

  // Zoom uses layout size + cursor/center anchoring. While Cmd/Ctrl is held,
  // native scroll is locked so the wheel only zooms (never pans or changes z).

  // Labels panel: whole-volume lifecycle summary (state/origin per id,
  // shared with 2D "Hide Verified" rendering) + 3D panel pinned ids.
  const [labelsSummaryRows, setLabelsSummaryRows] = useState<LabelSummaryRow[]>([]);
  /** Which Labels list is showing. Lives here, not in `LabelsPanel`, because
   * the Select tool follows the same This layer / All rule (see `selectLabel`). */
  const [labelsScope, setLabelsScope] = useState<LabelsScope>("all");
  const labelsScopeRef = useRef(labelsScope);
  labelsScopeRef.current = labelsScope;
  const labelsSummaryRowsRef = useRef(labelsSummaryRows);
  labelsSummaryRowsRef.current = labelsSummaryRows;
  const [labelsSummaryLoading, setLabelsSummaryLoading] = useState(false);
  // Default OFF so all labels are visible on open (Hide Verified is an
  // opt-in filter, sitting beside Filters Options — not buried inside it).
  const [hideVerified, setHideVerified] = useState(false);
  const [lifecycleError, setLifecycleError] = useState<string | null>(null);
  // 3D pin set — the single source of truth for what the 3D panel loads (see
  // `label3DIds`). Seeded with `initialSoloId` so the share page opens with
  // the shared label already in 3D instead of an empty scene.
  const [pinned3D, setPinned3D] = useState<Set<number>>(
    () => new Set(initialSoloId != null && initialSoloId > 0 ? [initialSoloId] : []),
  );
  // Labels list summary (This slice / All) — bumped on Save and Labels → Refresh.
  const [labelsSummaryToken, setLabelsSummaryToken] = useState(0);
  /** Bumped only where an *exact* ROI membership answer is wanted (Labels →
   * Refresh, Reset labels) — see the fetch effect for why Save does not. */
  const [regionMembershipToken, setRegionMembershipToken] = useState(0);
  // 3D mesh rebuild — ONLY from Labels-section 3D actions (pin / 3D slice /
  // 3D all). Save and paint tools must not rebuild the 3D view.
  const [labels3DRefreshKey, setLabels3DRefreshKey] = useState(0);

  const imgRef = useRef<HTMLImageElement | null>(null);
  const overlayRef = useRef<HTMLCanvasElement | null>(null);
  /** Hover cursors only — never CSS-masked, so Flood fill / brush rings stay
   * visible over the full image even when Region-only masks the label overlay. */
  const cursorLayerRef = useRef<HTMLCanvasElement | null>(null);
  const trackPromptCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const trackUndoRef = useRef<TrackingPromptGeometrySnapshot[]>([]);
  const trackRedoRef = useRef<TrackingPromptGeometrySnapshot[]>([]);
  const trackPromptDraftRef = useRef<{ key: string; mask: Uint8Array } | null>(null);
  const trackPromptProposalRef = useRef<{ key: string; mask: Uint8Array } | null>(null);
  const trackPromptPointsRef = useRef<{ key: string; points: AiPoint[] }>({ key: "", points: [] });
  const trackPromptPredictSeqRef = useRef(0);
  const trackPromptPredictingRef = useRef(false);
  const trackPromptPredictionPromiseRef = useRef<Promise<void> | null>(null);
  const trackPromptSavePromiseRef = useRef<Promise<boolean> | null>(null);
  const trackPromptSaveChainRef = useRef<Promise<boolean>>(Promise.resolve(true));
  const trackPromptCommitPromiseRef = useRef<Promise<boolean> | null>(null);
  /** `commitTrackingProposal` is defined below `propagateTrackingQueue`, which
   * has to flush a live proposal before it sends the batch. Same
   * assign-during-render pattern as `roiOnlyRef` above. */
  const commitTrackingProposalRef = useRef<() => Promise<boolean>>(() => Promise.resolve(false));
  /** The parents a *local* (plan-based) propagation is waiting on review for,
   * plus the compound Reject restores. Cleared by Confirm and Reject alike. */
  const trackPreviewUndoRef = useRef<{ parentIds: number[]; edits: CompoundSliceEdit[] } | null>(null);
  const trackPromptFinalizeWhenReadyRef = useRef(false);
  const trackPromptDrawingRef = useRef(false);
  const trackPromptLastRef = useRef<[number, number] | null>(null);
  const trackPromptBoxRef = useRef<BoxDrag | null>(null);
  const trackPromptHoverRef = useRef<[number, number] | null>(null);
  const trackPromptHoverLabelRef = useRef<0 | 1>(1);
  /** Non-scrolling shell — fit size is measured here so scrollbar gutters
   * inside the viewport cannot change the fit base mid-zoom. */
  const shellRef = useRef<HTMLDivElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  /** Fit baseline (zoom=1 size + pad). Frozen across zoom — only Fit / open / real shell resize recomputes it. */
  const fitBaseRef = useRef<{ w: number; h: number; padX: number; padY: number }>({
    w: 0,
    h: 0,
    padX: 0,
    padY: 0,
  });
  const lastShellRef = useRef({ w: 0, h: 0 });
  /** Keep image point under the cursor/center stable across a zoom step. */
  const zoomAnchorRef = useRef<{
    offsetX: number;
    offsetY: number;
    localX: number;
    localY: number;
    oldW: number;
  } | null>(null);
  /** While Cmd/Ctrl is held, pin scroll here so the wheel cannot pan. */
  const cmdScrollLockRef = useRef<{ left: number; top: number } | null>(null);
  const stageLayoutRef = useRef<{
    stageW: number;
    stageH: number;
    contentW: number;
    contentH: number;
    stageLeft: number;
    stageTop: number;
  } | null>(null);
  /** Pending Fit window / Fit width — applied after the next stage layout. */
  const pendingFitRef = useRef<"window" | "width" | null>(null);
  /**
   * Center once on page entry. Stays true across early shell resizes (Annotate
   * side rails settling) so we don't lock scroll before the final canvas size.
   * Cleared after a stable center, or as soon as the user zooms/pans/Fits.
   */
  const needsOpenCenterRef = useRef(true);
  /** Set when layoutStage just forced a fit-center (skip restoring an old pan). */
  const justForcedCenterRef = useRef(false);

  // View ↔ Annotate remounts usually, but if `editable` flips in place, re-run
  // the one-shot open center for the new chrome width.
  useEffect(() => {
    needsOpenCenterRef.current = true;
    fitBaseRef.current = { w: 0, h: 0, padX: 0, padY: 0 };
    lastShellRef.current = { w: 0, h: 0 };
    setFitEpoch((e) => e + 1);
  }, [editable]);
  /** Bumped on every Fit click so re-fitting at zoom=1 / same mode still relayouts. */
  const [fitEpoch, setFitEpoch] = useState(0);
  const [stageLayout, setStageLayout] = useState<{
    stageW: number;
    stageH: number;
    contentW: number;
    contentH: number;
    stageLeft: number;
    stageTop: number;
  } | null>(null);
  stageLayoutRef.current = stageLayout;
  const idsRef = useRef<Int32Array | null>(null); // current slice, flat h*w
  const shapeRef = useRef<[number, number]>([0, 0]); // [h, w]
  const imageDataRef = useRef<ImageData | null>(null); // reused across renders to avoid per-stroke allocation
  // Per-slice undo/redo — survives Save and z-navigation. Undo after Save
  // marks the slice dirty again (needs another Save). Cleared only on
  // whole-volume mutations (Split / Watershed / Track via forceServer reload).
  // Stack containers are copied on stash/restore so a pop on one slice cannot
  // empty another slice's parked history.
  const historyRef = useRef(new SliceHistory());
  /** The compound most recently written by `applyPendingToolPlan` — Track's
   * Reject restores its `before` planes. */
  const lastAppliedPlanRef = useRef<CompoundSliceEdit[] | null>(null);
  const drawingRef = useRef(false);
  const lastPointRef = useRef<[number, number] | null>(null);
  const nextIdRef = useRef(1);
  const aiPointsRef = useRef<AiPoint[]>([]);
  const aiPreviewRef = useRef<AiPreview | null>(null);
  const boxDragRef = useRef<BoxDrag | null>(null);
  // Last hovered image-space pixel (null when the pointer is off-canvas) —
  // drives the box/box-erase crosshair (#25 item D) and the brush/erase
  // size cursor (#25 item G), both redrawn via the cheap `renderCursorOverlay`
  // path below rather than the full per-pixel label recompute.
  const hoverPosRef = useRef<[number, number] | null>(null);
  const roiWarmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastRoiWarmRef = useRef("");
  // The Point Mask/Boundary transient cursor tip (#27) — Cellable's `line`
  // rubber-band tip: null until ≥1 point is committed (no preview at all
  // before the first click, matching Cellable's `if not self.current:
  // return`), then tracks the cursor with label flipped by Shift on every
  // move. Not in `aiPointsRef` — it's never committed until a click/Ctrl-
  // click/Enter promotes it.
  const aiTipRef = useRef<AiPoint | null>(null);
  // Cursor-follow live predict (#27), coalesced over HTTP (#28/#29) — an
  // earlier version aborted the in-flight predict on every pointer move
  // (Cellable's own "predict on every repaint" is an in-process call, not a
  // network round trip); that left the green mask visibly frozen, since the
  // constantly-superseded request never got a chance to land. Coalescing
  // instead: at most one predict in flight at a time, `dirty` marks that a
  // newer tip arrived while it was running, and the `finally` below fires
  // exactly one follow-up request for the latest position once the current
  // one finishes — never a queue, never an abort-storm. Both live and
  // committed-only predicts still share the single `aiSeqRef`/`aiAbortRef`
  // guard above, so a click can never be overwritten by a stale hover
  // response or vice versa.
  const livePredictRef = useRef<{ inFlight: boolean; dirty: boolean }>({
    inFlight: false,
    dirty: false,
  });
  // `runPredictPointsWith` chains its own follow-up call from inside its
  // `finally` block (see `livePredictRef.dirty` above) — going through a ref
  // instead of calling itself by name sidesteps a stale-closure trap: a
  // `useCallback`'s own body captures the closure that existed when *that*
  // instance was created, but a ref is always read fresh, so a chained call
  // always dispatches through whichever version of the function is current.
  const runPredictPointsWithRef = useRef<(pts: AiPoint[], opts?: { silent?: boolean; live?: boolean }) => Promise<void>>(
    async () => undefined,
  );
  // Which committed prompt point (index into `aiPointsRef`) is being
  // dragged, if any (#29 item U8) — `null` when not dragging. A separate
  // ref from `drawingRef` on purpose: `drawingRef` drives the generic
  // brush-stroke/box-drag commit path in `onPointerUp`, and a dragged AI
  // prompt point must never fall into that path (it doesn't touch `idsRef`
  // at all, so there's nothing to `commit()` to the server).
  const draggingPointIdxRef = useRef<number | null>(null);
  // Offscreen canvas holding the *undisplayed* (no CSS brightness/contrast
  // filter) intensity image for the current slice — repopulated whenever
  // the `<img>` finishes loading a new slice (its `onLoad`). Lets the
  // status readout (#29 item U3) read a per-pixel intensity value via
  // `getImageData` without re-fetching or re-decoding anything.
  const intensityCtxRef = useRef<CanvasRenderingContext2D | null>(null);
  // Direct DOM write for the status readout — updated on every pointer move
  // over the canvas, which is far too high-frequency to route through React
  // state (same reasoning as the overlay canvas itself: see `renderOverlay`/
  // `renderCursorOverlay` above).
  const statusReadoutRef = useRef<HTMLSpanElement | null>(null);
  const contextMenuRef = useRef<HTMLDivElement | null>(null);
  const prevPaintToolRef = useRef<PaintTool>("select");
  // Guards a rapid click/re-box from letting an older, slower predict
  // response overwrite a newer one (Cellable: "keep last-good preview
  // until the newer one arrives") — each predict call captures the
  // post-increment sequence number and checks it's still current before
  // applying its result; a superseded call's abort() also drops the
  // network request itself. `committingAiRef` is a second, narrower guard
  // against a double Enter/click re-entering `commitAiPreview` mid-flight
  // (Cellable's `_finaliseInProgress`).
  const aiSeqRef = useRef(0);
  const aiAbortRef = useRef<AbortController | null>(null);
  const committingAiRef = useRef(false);
  // A Box double-click may arrive while its prediction request is still in
  // flight. Remember the user's intent and commit as soon as that preview
  // becomes available.
  const finalizeBoxWhenReadyRef = useRef(false);

  // Seed next-id once per task. Do NOT reset activeId on later refreshes —
  // that stole the user's selection / New reservation and felt unstable.
  // An explicit `initialActiveId` (the share page's shared label) is a
  // caller-chosen selection, not a default to overwrite: seeding "next new
  // id" over it is what left the shared page Active on max+1 instead of the
  // shared instance (03 item A, "Labels parity").
  const labelStateSeededRef = useRef(initialActiveId != null);
  useEffect(() => {
    labelStateSeededRef.current = initialActiveId != null;
  }, [taskId, initialActiveId]);
  useEffect(() => {
    if (!labelState.data) return;
    const serverNext = Math.max(1, labelState.data.next_label_id);
    // Never shrink the local counter (covers unsaved New / painted ids).
    nextIdRef.current = Math.max(nextIdRef.current, serverNext);
    if (!labelStateSeededRef.current) {
      labelStateSeededRef.current = true;
      setActiveId(serverNext);
    }
  }, [labelState.data, taskId]);

  const axisLen = axisLength(meta.data?.shape, axis);

  const refreshLabelsSummary = useCallback(() => {
    setLabelsSummaryLoading(true);
    api.getLabelsSummary(taskId)
      .then((res) => setLabelsSummaryRows(res.labels ?? []))
      .catch(() => setLabelsSummaryRows([]))
      .finally(() => setLabelsSummaryLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  useEffect(() => {
    refreshLabelsSummary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshLabelsSummary, labelsSummaryToken]);

  // After the first slice decode, refresh "All" once more. Summary can race
  // ahead of the working-copy seed that getLabelIds triggers; without this,
  // "This slice" populates while "All" stays empty until a manual Refresh.
  const summaryReseededRef = useRef(false);
  useEffect(() => {
    summaryReseededRef.current = false;
  }, [taskId]);
  useEffect(() => {
    if (summaryReseededRef.current) return;
    if (instances.length === 0) return;
    if (labelsSummaryRows.length > 0) {
      summaryReseededRef.current = true;
      return;
    }
    if (labelsSummaryLoading) return;
    summaryReseededRef.current = true;
    refreshLabelsSummary();
  }, [instances, labelsSummaryRows.length, labelsSummaryLoading, refreshLabelsSummary]);

  // Volume-wide "which instances reach the ROI". Fetched only when something
  // actually filters by it.
  //
  // Deliberately *not* refreshed on every Save. The server answer costs one
  // read of the ROI's planes (seconds on a 2k x 2k volume; cached per label
  // file after that), and a Save changes the label file's mtime, so refetching
  // there would pay it again after every checkpoint. It is not needed either:
  //
  //   - a label painted into the ROI shows immediately, because the per-plane
  //     half of the union sees it in the live buffer;
  //   - a label erased out of the ROI stays *visible* until the next refresh,
  //     which is the safe direction — Region only showing one label too many
  //     is recoverable, hiding one the annotator is working on is not.
  //
  // Labels -> Refresh and Reset labels both bump the token below when an exact
  // answer is wanted.
  const regionMembershipWanted =
    Boolean(meta.data?.has_region_mask) && (roiOnly || hideOutsideRegion);
  useEffect(() => {
    if (!regionMembershipWanted) return;
    let alive = true;
    api.getRegionLabelIds(taskId)
      .then((res) => {
        if (!alive) return;
        // `has_region: false` and an empty id list mean different things: the
        // first must not filter at all, the second must hide everything.
        setRoiVolumeIds(res.has_region ? new Set(res.ids) : null);
      })
      .catch(() => {
        // Degrade to the per-plane answer rather than to "hide nothing" or a
        // dead panel — the ROI clip still bounds what is drawn.
        if (alive) setRoiVolumeIds(null);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [regionMembershipWanted, taskId, regionMembershipToken]);

  // Cellable's "Hide Verified" checkbox hides VERIFIED labels from the
  // 2D (and 3D) views, not just the Labels list — needs the whole-volume
  // state summary above, not just what's decoded on this slice.
  const verifiedIds = useMemo(
    () => new Set(labelsSummaryRows.filter((r) => r.state === "verified").map((r) => r.id)),
    [labelsSummaryRows],
  );

  // Heavy pass: recompute the per-pixel label/preview fill (labels + the
  // green AI-preview fill) and blit it. Only needed when that fill actually
  // changed (ids, preview mask, visibility, active id, ...) — reuses the
  // same ImageData/backing buffer across calls (cuts GC churn to zero for
  // the common case of painting on a slice whose dimensions haven't
  // changed since the last frame).
  const computeBaseImage = useCallback(() => {
    const canvas = overlayRef.current;
    const painted = idsRef.current;
    const [h, w] = shapeRef.current;
    if (!canvas || !painted || h === 0 || w === 0) return null;
    // What the annotator should *see*. Identical to `painted` unless Region
    // only recorded edits outside the ROI and has since been switched off with
    // "Empty voxels only" selected — then those pixels are shown as the stored
    // label they landed on. The paint buffer itself is never touched, so
    // toggling the mode (or the policy) back re-derives the other view from
    // the same work. See `outsideRegionEdits.ts`.
    const planeIndex = idsIndexRef.current;
    const outsideEdits =
      planeIndex != null ? outsideEditsRef.current.peek(planeIndex) : undefined;
    const ids =
      outsideEdits?.project(painted, {
        regionOnly: roiOnly,
        overwriteMode: regionOverwriteMode,
      }) ?? painted;
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    let image = imageDataRef.current;
    if (!image || image.width !== w || image.height !== h) {
      image = ctx.createImageData(w, h);
      imageDataRef.current = image;
    }
    // Region only, display side: an instance that overlaps the ROI *anywhere in
    // the volume* is drawn whole on every plane, one that never does is
    // dropped. Two sources, unioned:
    //
    //   - `roiVolumeIds`, the server's whole-volume answer. Only it knows about
    //     planes this browser has never loaded — deciding from the current
    //     plane alone is what used to make a long mito flicker in and out as
    //     the annotator scrubbed along it.
    //   - the current plane, recomputed per repaint on purpose: `ids` is the
    //     live paint buffer, so an instance drawn into the ROI appears the
    //     moment the stroke touches it, without waiting for a Save.
    //
    // `null` means "not filtering": Region only is off, or neither source is
    // available yet (the `mask-image` clip below still covers that gap).
    const regionBits =
      roiOnly && regionMaskBitsRef.current?.url === regionMaskUrl
        ? regionMaskBitsRef.current
        : null;
    const planeRegionIds =
      regionBits && regionBits.mask.length === ids.length
        ? labelIdsTouchingRegion(ids, regionBits.mask)
        : null;
    // Re-seed from a *new* server answer; otherwise keep growing the set this
    // session has accumulated, so nothing already revealed flickers away.
    if (regionMembershipBaseRef.current !== roiVolumeIds) {
      regionMembershipBaseRef.current = roiVolumeIds;
      regionTouchingIdsRef.current = regionMembership(roiVolumeIds, planeRegionIds);
    } else if (planeRegionIds) {
      const known = regionTouchingIdsRef.current;
      if (known) for (const id of planeRegionIds) known.add(id);
      else regionTouchingIdsRef.current = regionMembership(roiVolumeIds, planeRegionIds);
    }
    const regionIds = roiOnly ? regionTouchingIdsRef.current : null;
    const showAiPreview = AI_PREVIEW_TOOLS.includes(paintTool);
    const preview =
      (showAiPreview ? aiPreviewRef.current : null) ??
      // An interpolation preview is proposed-but-unwritten geometry exactly
      // like an AI proposal, so it gets the same green fill + white contour
      // rather than a second visual language for the same idea.
      (paintTool === "interpolate"
        ? interpPreviewMaskFor(
            interpPreviewRef.current,
            axisRef.current,
            idsIndexRef.current,
            ids.length,
          )
        : null);
    for (let i = 0; i < ids.length; i++) {
      const id = ids[i];
      const o = i * 4;
      if (preview && preview.mask[i]) {
        // Proposed mask = green translucent fill (Cellable's
        // select_fill_color + preview label_opacity≈0.5); the opaque white
        // contour is a vector overlay drawn on top, see drawVectorOverlay.
        image.data[o] = 0;
        image.data[o + 1] = 255;
        image.data[o + 2] = 0;
        image.data[o + 3] = AI_PREVIEW_FILL_ALPHA;
        continue;
      }
      const suppressed =
        id <= 0 ||
        (soloId != null ? id !== soloId : hiddenIds.has(id)) ||
        (hideVerified && verifiedIds.has(id)) ||
        (regionIds !== null && !regionIds.has(id));
      if (suppressed) {
        image.data[o + 3] = 0;
        continue;
      }
      const [r, g, b] = labelColor(id);
      image.data[o] = r;
      image.data[o + 1] = g;
      image.data[o + 2] = b;
      // Global committed-label opacity (#29 item U5, Cellable's
      // `label_opacity_slider`) — scales the committed alpha only; the AI
      // proposal fill above is intentionally untouched by it.
      image.data[o + 3] = Math.round((id === activeId ? 220 : LABEL_ALPHA) * (labelOpacity / 100));
    }
    ctx.putImageData(image, 0, 0);
    return { ctx, canvas, w, h };
    // `interpPreviewCount` is not read here — it is the state mirror of
    // `interpPreviewRef`, listed so planning/cancelling a preview repaints.
    // `regionMaskBitsUrl` is the state mirror of `regionMaskBitsRef`, listed
    // so a plane repaints the moment its ROI finishes decoding.
  }, [
    activeId,
    hiddenIds,
    soloId,
    hideVerified,
    verifiedIds,
    paintTool,
    labelOpacity,
    interpPreviewCount,
    roiOnly,
    roiVolumeIds,
    regionMaskUrl,
    regionMaskBitsUrl,
    regionOverwriteMode,
    outsideEditRevision,
  ]);

  // Vector overlays on top of whatever fill is already blitted — proper
  // alpha compositing (unlike a second putImageData, which would replace
  // rather than blend). Sized in **screen space**, not image space —
  // Cellable's `shape.py` draws vertices/pen strokes at a constant
  // on-screen size regardless of zoom (`point_size≈8`, `PEN_WIDTH≈2`); a
  // fixed image-space radius would shrink to sub-pixel on a large EM slice
  // at fit-window. `scale` here is the *actual* rendered-CSS-pixels-per-
  // image-pixel ratio (folds in both the explicit zoom control *and* the
  // fit-window/fit-width auto-scaling, which `zoom` alone doesn't capture),
  // measured fresh every repaint via the canvas's real layout box.
  //
  // Deliberately callable on its own (see `renderCursorOverlay` below) so
  // high-frequency pointer moves (box crosshair, brush/erase size cursor)
  // never have to pay for `computeBaseImage`'s O(h*w) label recompute —
  // they just re-blit the cached ImageData and redraw these vectors.
  const drawVectorOverlay = useCallback(
    (ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement, w: number, h: number) => {
      const screenRect = canvas.getBoundingClientRect();
      const scale = screenRect.width > 0 ? screenRect.width / w : 1;
      const toImagePx = (screenPx: number) => screenPx / Math.max(scale, 0.001);
      const pointRadius = toImagePx(6); // ~12px on-screen diameter — denser on EM
      const penWidth = Math.max(toImagePx(2), 0.5);

      // Proposed-mask contour: opaque white outline of the AI preview,
      // Cellable's `select_line_color` (shape.py `_mask_outline_path`).
      const preview =
        (AI_PREVIEW_TOOLS.includes(paintTool) ? aiPreviewRef.current : null) ??
        (paintTool === "interpolate"
          ? interpPreviewMaskFor(
              interpPreviewRef.current,
              axisRef.current,
              idsIndexRef.current,
              h * w,
            )
          : null);
      if (preview) {
        strokeMaskContour(ctx, preview.mask, h, w, Math.max(toImagePx(2.5), 0.5), AI_PREVIEW_CONTOUR_COLOR);
      }

      if (AI_POINT_TOOLS.includes(paintTool)) {
        const lastCommitted = aiPointsRef.current[aiPointsRef.current.length - 1];
        const tip = aiTipRef.current;
        if (lastCommitted && tip) {
          strokeHiVis(ctx, Math.max(toImagePx(2.5), 1), tip.label === 1 ? "#22c55e" : "#ef4444", () => {
            ctx.beginPath();
            ctx.moveTo(lastCommitted.x, lastCommitted.y);
            ctx.lineTo(tip.x, tip.y);
          });
        }
        const pts = tip ? [...aiPointsRef.current, tip] : aiPointsRef.current;
        for (const p of pts) {
          const color = p.label === 1 ? "#22c55e" : "#ef4444";
          ctx.beginPath();
          ctx.arc(p.x, p.y, pointRadius * 1.35, 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.fill();
          strokeHiVis(ctx, Math.max(toImagePx(2), 1), "#ffffff", () => {
            ctx.beginPath();
            ctx.arc(p.x, p.y, pointRadius * 1.35, 0, Math.PI * 2);
          });
        }
      }
      if (paintTool === "box_mask" || paintTool === "box_eraser") {
        // Rubber-band rectangle only — the hover crosshair is painted on the
        // unmasked cursor layer so Region-only masking cannot hide it.
        const box = boxDragRef.current;
        if (box) {
          const bx = Math.min(box.x0, box.x1);
          const by = Math.min(box.y0, box.y1);
          const bw = Math.abs(box.x1 - box.x0);
          const bh = Math.abs(box.y1 - box.y0);
          const color = paintTool === "box_mask" ? "#f59e0b" : "#38bdf8";
          strokeHiVis(ctx, Math.max(penWidth * 1.4, toImagePx(2.5)), color, () => {
            ctx.beginPath();
            ctx.rect(bx, by, bw, bh);
          });
          const hs = toImagePx(5);
          ctx.fillStyle = color;
          for (const [cx, cy] of [
            [bx, by],
            [bx + bw, by],
            [bx, by + bh],
            [bx + bw, by + bh],
          ] as const) {
            ctx.fillRect(cx - hs, cy - hs, hs * 2, hs * 2);
          }
        }
      }
      if (paintTool === "seeds") {
        const arm = toImagePx(10);
        const seedR = toImagePx(5);
        for (const s of wsSeeds) {
          const sc = sliceCoordsFromVoxel(axis, index, s);
          if (!sc) continue;
          ctx.beginPath();
          ctx.arc(sc.px, sc.py, seedR, 0, Math.PI * 2);
          ctx.fillStyle = "#facc15";
          ctx.fill();
          strokeHiVis(ctx, Math.max(toImagePx(2.5), 1.2), "#facc15", () => {
            ctx.beginPath();
            ctx.moveTo(sc.px - arm, sc.py);
            ctx.lineTo(sc.px + arm, sc.py);
            ctx.moveTo(sc.px, sc.py - arm);
            ctx.lineTo(sc.px, sc.py + arm);
          });
        }
      }
    },
    [paintTool, wsSeeds, index],
  );

  /** Custom overlay cursors — brush/erase rings, box crosshairs, point reticle. */
  const paintToolCursor = useCallback(() => {
    const canvas = cursorLayerRef.current;
    const overlay = overlayRef.current;
    const [h, w] = shapeRef.current;
    if (!canvas || !overlay || h === 0 || w === 0) return;
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    if (!usesCustomOverlayCursor(paintTool)) return;
    const hover = hoverPosRef.current;
    if (!hover || !editable || swapped) return;
    const screenRect = canvas.getBoundingClientRect();
    const scale = screenRect.width > 0 ? screenRect.width / w : 1;
    const toImagePx = (screenPx: number) => screenPx / Math.max(scale, 0.001);
    const [hy, hx] = hover;
    if (paintTool === "point_mask" || paintTool === "boundary") {
      const color = paintTool === "point_mask" ? "#22c55e" : "#38bdf8";
      const radius = paintTool === "point_mask" ? brushSize : eraserSize;
      drawPointPromptCursor(
        ctx,
        hx,
        hy,
        color,
        Math.max(toImagePx(2), 1),
        radius * 1.25,
        radius,
      );
      return;
    }
    if (paintTool === "box_mask" || paintTool === "box_eraser") {
      const color = paintTool === "box_mask" ? "#f59e0b" : "#38bdf8";
      drawCrosshairCursor(ctx, hx, hy, w, h, color, Math.max(toImagePx(2.5), 1.2), toImagePx(3.5));
      return;
    }
    if (paintTool === "brush" || paintTool === "eraser") {
      const color = paintTool === "brush" ? "#22c55e" : "#38bdf8";
      const size = paintTool === "brush" ? brushSize : eraserSize;
      // +0.5 puts the cursor on the hovered pixel's centre rather than its
      // top-left corner, so the ring and the painted disc coincide.
      drawBrushCursor(
        ctx,
        hx + 0.5,
        hy + 0.5,
        brushRadius(size),
        color,
        Math.max(toImagePx(3), 1.5),
        toImagePx(3.5),
        cursorStyle,
      );
    }
  }, [paintTool, brushSize, eraserSize, editable, swapped, cursorStyle]);

  const renderOverlay = useCallback(() => {
    const base = computeBaseImage();
    if (!base) return;
    drawVectorOverlay(base.ctx, base.canvas, base.w, base.h);
    paintToolCursor();
    // zoom/fitMode aren't read directly in this body, but a zoom/fit change
    // alters the canvas's on-screen size, which changes `scale` inside
    // drawVectorOverlay's toImagePx — including them here forces this
    // callback to change identity so the `useEffect(renderOverlay)` below
    // re-fires and point/contour/crosshair sizing stays screen-constant
    // instead of stale from before the resize.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [computeBaseImage, drawVectorOverlay, paintToolCursor, zoom, fitMode]);

  // Cheap pass for high-frequency pointer moves: re-blit the cached fill
  // (no O(h*w) label recompute) and redraw only the vectors. Used for box
  // rubber-band dragging and hover-only cursor feedback (crosshair, brush
  // size circle) so scrubbing the mouse around never re-touches the label
  // loop unless the underlying ids/preview actually changed.
  const renderCursorOverlay = useCallback(() => {
    const canvas = overlayRef.current;
    const image = imageDataRef.current;
    const [h, w] = shapeRef.current;
    if (!canvas || !image || h === 0 || w === 0) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.putImageData(image, 0, 0);
    drawVectorOverlay(ctx, canvas, w, h);
    paintToolCursor();
  }, [drawVectorOverlay, paintToolCursor]);

  const refreshInstances = useCallback(() => {
    const ids = idsRef.current;
    setInstances(ids ? uniqueInstances(ids) : []);
  }, []);

  // History lives in a ref (mutated imperatively so painting doesn't
  // trigger a re-render on every stroke) — call after any mutation so the
  // Undo/Redo buttons' enabled state actually reflects them.
  const syncHistoryCounts = useCallback(() => {
    setUndoCount(historyRef.current.undoCount);
    setRedoCount(historyRef.current.redoCount);
  }, []);

  const stashHistoryForZ = useCallback((z: number) => {
    historyRef.current.stash(z);
  }, []);

  const restoreHistoryForZ = useCallback((z: number) => {
    historyRef.current.restore(z);
  }, []);

  const clearAllHistory = useCallback(() => {
    historyRef.current.clearAll();
  }, []);

  const stashCurrentSlice = useCallback(() => {
    const ids = idsRef.current;
    const z = idsIndexRef.current;
    if (!ids || z == null || !currentSliceDirtyRef.current) return;
    // Freeze a copy so later edits on another z cannot mutate this stash.
    pendingSlicesRef.current.freeze(z, ids);
    stashHistoryForZ(z);
  }, [stashHistoryForZ]);


  // --- Slice navigation cache (progress/history/03 item C) -----------------
  //
  // Revisiting a slice is the single most common thing anyone does here
  // (scrub back and forth, step z while checking one instance), and it used
  // to cost a full JPEG re-fetch + label round trip every single time.
  //
  //   * `sliceImgCacheRef` — object URLs for decoded image slices. An image
  //     slice cannot change while the page is open, so entries are valid for
  //     the session; the map is a small LRU and revokes what it evicts.
  //   * `sliceRunsCacheRef` — server label RLE per slice. Small (a few KB), so
  //     more entries fit; dropped whenever anything writes labels, since a
  //     stale one would show pre-edit labels.
  //   * `sliceImgInflightRef` — de-dupes a foreground load and a prefetch
  //     racing for the same slice into one request.
  //
  // Every key is `axis:index`, never the index alone: with the Axial/Coronal/
  // Sagittal selector, "slice 5" names three different planes, and an
  // index-only key hands the z-plane back while the user is looking at y.
  const sliceImgCacheRef = useRef<Map<string, string>>(new Map());
  const sliceRunsCacheRef = useRef<Map<string, LabelIdsResponse>>(new Map());
  // Invalidating a Map does not stop a pre-save request from finishing later
  // and putting its stale response back. This revision gate makes any read
  // overlapping a write retry against the post-write working mask.
  const labelReadRevisionRef = useRef(new RevisionedFetch());
  const sliceImgInflightRef = useRef<Map<string, Promise<string>>>(new Map());
  const sliceKey = useCallback((a: Axis, index: number) => `${a}:${index}`, []);

  /** One controller for every prefetch this canvas ever fires — aborted only
   * on unmount (see the prefetch effect for why not per navigation). */
  const prefetchAbortRef = useRef<AbortController>(new AbortController());

  useEffect(() => {
    chunkRendererRef.current?.dispose();
    chunkRendererRef.current = null;
    chunkFallbackRef.current = false;
    setRendererNotice(null);
    if (
      !phase14ChunkRendererEnabled() ||
      !meta.data ||
      meta.data.ready_streaming !== true ||
      !api.chunkEndpoints
    ) {
      if (
        phase14ChunkRendererEnabled() &&
        meta.data &&
        meta.data.ready_streaming !== true &&
        api.chunkEndpoints
      ) {
        setRendererNotice("Streaming pyramid is not ready; using the original source.");
      }
      return;
    }
    const source = new ChunkRenderedImageSource({
      volumeId,
      deployment: window.location.origin,
      authorizationScope: chunkAuthorizationScope,
      meta: meta.data,
      endpoints: api.chunkEndpoints,
    });
    chunkRendererRef.current = source;
    setChunkRendererRevision((revision) => revision + 1);
    return () => {
      source.dispose();
      if (chunkRendererRef.current === source) chunkRendererRef.current = null;
    };
  }, [api, chunkAuthorizationScope, meta.data, volumeId]);

  useEffect(() => {
    regionRendererRef.current?.dispose();
    regionRendererRef.current = null;
    regionFallbackRef.current = false;
    if (
      !phase14ChunkRendererEnabled() ||
      !meta.data?.has_region_mask ||
      meta.data.region_ready_streaming !== true ||
      !api.chunkEndpoints
    ) return;
    const source = new ChunkRenderedImageSource({
      volumeId,
      deployment: window.location.origin,
      authorizationScope: chunkAuthorizationScope,
      meta: meta.data,
      layer: "region",
      endpoints: api.chunkEndpoints,
    });
    regionRendererRef.current = source;
    setChunkRendererRevision((revision) => revision + 1);
    return () => {
      source.dispose();
      if (regionRendererRef.current === source) regionRendererRef.current = null;
    };
  }, [api, chunkAuthorizationScope, meta.data, volumeId]);

  // Decode this plane's ROI overlay into a per-pixel mask, but only while
  // Region only is on — it is the one mode that needs the ROI as data, and
  // decoding a plane nobody is filtering would cost a canvas readback per
  // slice for nothing. Until it resolves, `computeBaseImage` leaves the
  // filter off and the `mask-image` clip below keeps labels inside the ROI,
  // so the ROI boundary is never crossed by something that shouldn't show.
  useEffect(() => {
    if (!roiOnly || !regionMaskUrl) {
      regionMaskBitsRef.current = null;
      regionTouchingIdsRef.current = null;
      regionMembershipBaseRef.current = null;
      setRegionMaskBitsUrl(null);
      return;
    }
    if (regionMaskBitsRef.current?.url === regionMaskUrl) return;
    const url = regionMaskUrl;
    let alive = true;
    const [h, w] = shapeRef.current;
    void decodeRegionMask(url, w, h)
      .then((mask) => {
        if (!alive) return;
        regionMaskBitsRef.current = { url, mask };
        const ids = idsRef.current;
        const plane = ids && ids.length === mask.length
          ? labelIdsTouchingRegion(ids, mask)
          : null;
        // Union, not replace: the volume-wide set is what keeps an instance
        // editable on the planes where it sits outside the ROI.
        regionTouchingIdsRef.current = regionMembership(roiVolumeIdsRef.current, plane);
        regionMembershipBaseRef.current = roiVolumeIdsRef.current;
        setRegionMaskBitsUrl(url);
      })
      .catch(() => {
        // A failed decode is not a reason to show labels the ROI excludes:
        // leaving the filter off falls back to the mask-image clip, which is
        // stricter than showing everything.
        if (alive) setRegionMaskBitsUrl(null);
      });
    return () => {
      alive = false;
    };
    // `sliceLoading` is not read here; it re-runs the effect once the plane's
    // shape is in `shapeRef`, which is a ref and cannot trigger this itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roiOnly, regionMaskUrl, sliceLoading]);

  // Image blobs outlive React state — release them when the canvas unmounts.
  useEffect(() => {
    const cache = sliceImgCacheRef.current;
    const prefetch = prefetchAbortRef.current;
    return () => {
      prefetch.abort();
      for (const url of cache.values()) URL.revokeObjectURL(url);
      cache.clear();
    };
  }, []);

  /** Re-fetch label ids from the server from now on (any write invalidates
   * every cached slice: 3D ops and Track rewrite whole volumes). */
  const invalidateSliceLabelCache = useCallback(
    (z?: number, a?: Axis) => {
      labelReadRevisionRef.current.invalidate();
      // Prefetches are never user-blocking; cancel all that started against
      // the pre-write mask instead of making each one retry. A fresh
      // controller is ready for the next navigation's prefetch batch.
      prefetchAbortRef.current.abort();
      prefetchAbortRef.current = new AbortController();
      const cache = sliceRunsCacheRef.current;
      if (z == null) {
        cache.clear();
        return;
      }
      // Writing plane `z` of axis `a` leaves the other planes *of that axis*
      // untouched — but every plane of the other two axes cuts through it, so
      // those are all potentially stale and have to go.
      const writtenAxis = a ?? axisRef.current;
      cache.delete(sliceKey(writtenAxis, z));
      for (const key of [...cache.keys()]) {
        if (!key.startsWith(`${writtenAxis}:`)) cache.delete(key);
      }
    },
    [sliceKey],
  );

  const sliceImageUrl = useCallback(
    (z: number, signal?: AbortSignal, forAxis?: Axis): Promise<string> => {
      const a = forAxis ?? axisRef.current;
      const key = `image:${sliceKey(a, z)}`;
      const cache = sliceImgCacheRef.current;
      const hit = cache.get(key);
      if (hit !== undefined) {
        cache.delete(key); // refresh LRU position
        cache.set(key, hit);
        return Promise.resolve(hit);
      }
      const inflight = sliceImgInflightRef.current.get(key);
      if (inflight) return inflight;
      const now = performance.now();
      const last = lastChunkNavigationRef.current;
      const foreground = a === axisRef.current && z === indexRef.current;
      const generation = foreground
        ? ++chunkGenerationRef.current
        : chunkGenerationRef.current;
      const moving =
        foreground &&
        last !== null &&
        last.axis === a &&
        last.index !== z &&
        now - last.at < 180;
      if (foreground) lastChunkNavigationRef.current = { axis: a, index: z, at: now };
      let selectedChunkSource: ChunkRenderedImageSource | null = null;
      const chunkRead = (async () => {
        let chunkSource = chunkFallbackRef.current
          ? null
          : chunkRendererRef.current;
        if (
          phase14ChunkRendererEnabled() &&
          api.chunkEndpoints &&
          !chunkFallbackRef.current &&
          !chunkSource
        ) {
          await Promise.resolve();
          chunkSource = chunkRendererRef.current;
        }
        selectedChunkSource = chunkSource;
        return chunkSource
          ? chunkSource.render({
            axis: a,
            index: z,
            generation,
            moving,
            signal,
            activateGeneration: foreground,
            onRefine: (fine) => {
              if (
                a !== axisRef.current ||
                z !== indexRef.current ||
                signal?.aborted
              ) return;
              const previous = cache.get(key);
              cache.delete(key);
              cache.set(key, fine.url);
              if (previous && previous !== fine.url) URL.revokeObjectURL(previous);
              if (imgRef.current) imgRef.current.src = fine.url;
            },
            }).then((frame) => frame.url)
          : fetchObjectUrl(api.imageSlicePath(volumeId, { axis: a, index: z }), signal);
      })();
      const p = chunkRead
        .catch(async (error) => {
          if (
            signal?.aborted ||
            (error instanceof DOMException && error.name === "AbortError") ||
            !selectedChunkSource
          ) throw error;
          // Ignore a superseded source (React StrictMode and volume switches
          // dispose one generation before mounting its replacement).
          if (
            signal?.aborted ||
            (selectedChunkSource &&
              chunkRendererRef.current !== selectedChunkSource)
          ) throw error;
          // One-way fallback for this viewer session prevents source flapping.
          chunkFallbackRef.current = true;
          chunkRendererRef.current?.dispose();
          chunkRendererRef.current = null;
          setRendererNotice(chunkFallbackMessage(error));
          return fetchObjectUrl(
            api.imageSlicePath(volumeId, { axis: a, index: z }),
            signal,
          );
        })
        .then((url) => {
          cache.set(key, url);
          while (cache.size > SLICE_IMG_CACHE_MAX) {
            const oldest = cache.keys().next().value as string;
            const evicted = cache.get(oldest);
            cache.delete(oldest);
            if (evicted) URL.revokeObjectURL(evicted);
          }
          return url;
        })
        .finally(() => sliceImgInflightRef.current.delete(key));
      sliceImgInflightRef.current.set(key, p);
      return p;
    },
    // `api` is stable per mount (module const, or memoized on the share page).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [volumeId, sliceKey],
  );

  const sliceRegionUrl = useCallback(
    (z: number, signal?: AbortSignal, forAxis?: Axis): Promise<string | null> => {
      if (!meta.data?.has_region_mask) return Promise.resolve(null);
      const a = forAxis ?? axisRef.current;
      const key = `region:${sliceKey(a, z)}`;
      const cache = sliceImgCacheRef.current;
      const hit = cache.get(key);
      if (hit !== undefined) {
        cache.delete(key);
        cache.set(key, hit);
        return Promise.resolve(hit);
      }
      const inflight = sliceImgInflightRef.current.get(key);
      if (inflight) return inflight;
      const fromSlice = () =>
        fetchObjectUrl(api.regionMaskSlicePath(volumeId, { axis: a, index: z }), signal);
      const source = regionFallbackRef.current ? null : regionRendererRef.current;
      const regionRead = source
        ? source
            .render({
              axis: a,
              index: z,
              // Same generation as the image plane it will be composited over:
              // an ROI from a different z is overlay drift, not a stale frame.
              generation: chunkGenerationRef.current,
              moving: false,
              signal,
              activateGeneration: false,
            })
            .then((frame) => frame.url)
            .catch((error) => {
              if (
                signal?.aborted ||
                (error instanceof DOMException && error.name === "AbortError") ||
                regionRendererRef.current !== source
              ) throw error;
              // One-way and ROI-only: the image layer keeps its transport.
              regionFallbackRef.current = true;
              regionRendererRef.current?.dispose();
              regionRendererRef.current = null;
              setRendererNotice(chunkFallbackMessage(error, "region"));
              return fromSlice();
            })
        : fromSlice();
      const p = regionRead
        .then((url) => {
          cache.set(key, url);
          while (cache.size > SLICE_IMG_CACHE_MAX * 2) {
            const oldest = cache.keys().next().value as string;
            const evicted = cache.get(oldest);
            cache.delete(oldest);
            if (evicted) URL.revokeObjectURL(evicted);
          }
          return url;
        })
        .finally(() => sliceImgInflightRef.current.delete(key));
      sliceImgInflightRef.current.set(key, p);
      return p;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [volumeId, sliceKey, meta.data?.has_region_mask],
  );

  const labelRunsFor = useCallback(
    async (z: number, signal?: AbortSignal, forAxis?: Axis): Promise<LabelIdsResponse> => {
      const a = forAxis ?? axisRef.current;
      const key = sliceKey(a, z);
      const cache = sliceRunsCacheRef.current;
      const hit = cache.get(key);
      if (hit !== undefined) return hit;
      const resp = await labelReadRevisionRef.current.loadLatest(() =>
        api.getLabelIds(taskId, a, z, signal),
      );
      cache.set(key, resp);
      while (cache.size > SLICE_RUNS_CACHE_MAX) {
        cache.delete(cache.keys().next().value as string);
      }
      return resp;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [taskId, sliceKey],
  );

  const loadSlice = useCallback(
    async (i: number, signal?: AbortSignal, opts?: { forceServer?: boolean }) => {
      if (!meta.data) return;
      const loadAxis = axisRef.current;
      // Render-only dependency churn used to re-enter this path while the
      // user was mid-stroke. Same plane + same buffer already loaded means
      // there is nothing to fetch unless a whole-volume write forced it.
      if (
        !opts?.forceServer &&
        idsIndexRef.current === i &&
        idsRef.current != null &&
        loadAxis === axisRef.current
      ) {
        return;
      }
      // A forced server reload after a whole-volume mutation invalidates every
      // cached label slice. Annotate tools apply to the pending buffer instead.
      if (opts?.forceServer) invalidateSliceLabelCache();
      setSliceLoading(true);
      try {
        const [imgUrl, regionUrl, resp] = await Promise.all([
          sliceImageUrl(i, signal),
          sliceRegionUrl(i, signal),
          labelRunsFor(i, signal),
        ]);
        if (signal?.aborted) return;
        // Drop stale responses after z OR axis moved on — including the image,
        // which must never be swapped in for a slice/view the user already left.
        if (i !== indexRef.current || loadAxis !== axisRef.current) return;
        // A stroke that finished after this fetch started owns the plane —
        // do not replace it with a pre-stroke server/cache response.
        if (
          !opts?.forceServer &&
          (drawingRef.current ||
            historyRef.current.hasOpenStroke ||
            currentSliceDirtyRef.current) &&
          idsIndexRef.current === i
        ) {
          return;
        }
        if (imgRef.current) imgRef.current.src = imgUrl;
        setRegionMaskUrl(regionUrl);
        const [h, w] = resp.shape;
        shapeRef.current = [h, w];
        if (opts?.forceServer) {
          // Whole-volume mutations make every local plane stale. Callers flush
          // first; clearing all entries here is the final guard against a
          // pre-operation plane being saved over the server result later.
          pendingSlicesRef.current.clear();
          // The outside-region records describe pending pixels; those are gone.
          outsideEditsRef.current.clear();
          dirtyRef.current = false;
          currentSliceDirtyRef.current = false;
          // The forced server state supersedes local history.
          clearAllHistory();
        }
        // What the server holds for this plane, before any local paint. Region
        // only needs it to remember what an outside edit covered; it is kept
        // for the current plane only (a frozen plane carries the values it
        // actually needs inside its own sparse record).
        const serverIds = decodeRuns(resp.runs, h * w);
        baselineIdsRef.current = serverIds;
        // Membership is volume-wide, so a new plane does not invalidate it —
        // but this plane's contribution has to be recomputed. Clearing the
        // seed marker makes the next repaint rebuild from `roiVolumeIds` plus
        // whatever this plane shows.
        regionTouchingIdsRef.current = null;
        regionMembershipBaseRef.current = null;
        const cached = pendingSlicesRef.current.get(i);
        if (cached && cached.length === h * w) {
          // Working copy — pending map keeps its own frozen stash.
          idsRef.current = cached.slice();
        } else {
          idsRef.current = serverIds.slice();
        }
        idsIndexRef.current = i;
        currentSliceDirtyRef.current = pendingSlicesRef.current.has(i);
        if (!opts?.forceServer) {
          restoreHistoryForZ(i);
        }
        // AI prompt points/preview are slice-specific (the underlying image
        // embedding is per-slice) — discard them on navigation, same as
        // Cellable resets `currentAIPromptPoints` on slice change.
        aiPointsRef.current = [];
        aiPreviewRef.current = null;
        aiTipRef.current = null;
        livePredictRef.current = { inFlight: false, dirty: false };
        // A deferred Box-Mask commit ("double-clicked while the prediction was
        // still running") belongs to the slice it was requested on. Clearing
        // hasAiPreview alone only postpones it: the intent would survive here
        // and then fire against the *next* prediction, committing a proposal
        // onto a slice the user never double-clicked.
        finalizeBoxWhenReadyRef.current = false;
        draggingPointIdxRef.current = null;
        setHasAiPreview(false);
        setAiPointCount(0);
        setAiError(null);
        boxDragRef.current = null;
        setTrackError(null);
        const stillDirty = pendingSlicesRef.current.size > 0;
        dirtyRef.current = stillDirty;
        setDirty(stillDirty);
        setStatus(stillDirty ? "dirty" : "idle");
        renderOverlay();
        refreshInstances();
        syncHistoryCounts();
      } catch (e) {
        if (
          !signal?.aborted &&
          !(e instanceof DOMException && e.name === "AbortError")
        ) throw e;
      } finally {
        setSliceLoading(false);
      }
    },
    [
      meta.data,
      sliceImageUrl,
      sliceRegionUrl,
      labelRunsFor,
      invalidateSliceLabelCache,
      renderOverlay,
      refreshInstances,
      syncHistoryCounts,
      clearAllHistory,
      restoreHistoryForZ,
    ],
  );

  // Keep the auto-load effect keyed only on plane identity. `loadSlice`'s
  // identity changes whenever overlay/render helpers change; putting it in
  // the dependency array would reload the plane mid-stroke and wipe paint.
  const loadSliceRef = useRef(loadSlice);
  loadSliceRef.current = loadSlice;
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void loadSliceRef.current(index, controller.signal);
    }, 100);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [index, axis, meta.data, chunkRendererRevision]);

  // Prefetch the neighbouring slices once this one has settled, so stepping
  // along the current axis hits the cache instead of the wire.
  // Deliberately *not* aborted when the index moves on: a prefetch that already
  // left is worth letting land — it's exactly the slice the user is heading
  // for — and cancelling it would also cancel a foreground load that de-duped
  // onto it. Only unmount (or axis change, via a new AbortController below)
  // stops it.
  // Switching axis makes any in-flight prefetch useless — cancel that batch.
  // Deliberately its own effect keyed on `axis` alone: rolling the controller
  // on every *index* change would also cancel a foreground load that de-duped
  // onto a prefetch promise.
  useEffect(() => {
    prefetchAbortRef.current.abort();
    prefetchAbortRef.current = new AbortController();
  }, [axis]);

  useEffect(() => {
    if (!meta.data) return;
    const signal = prefetchAbortRef.current.signal;
    const timer = setTimeout(() => {
      for (const z of [index + 1, index - 1, index + 2]) {
        if (z < 0 || z >= axisLen) continue;
        void sliceImageUrl(z, signal).catch(() => {});
        void labelRunsFor(z, signal).catch(() => {});
        // The ROI is prefetched alongside the image only while it streams:
        // over the fallback path this would be three extra full-plane PNGs per
        // navigation, which is the cost the chunk transport exists to avoid.
        if (regionRendererRef.current && !regionFallbackRef.current) {
          void sliceRegionUrl(z, signal).catch(() => {});
        }
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [index, axis, axisLen, meta.data, sliceImageUrl, labelRunsFor, sliceRegionUrl]);

  // Warm the EfficientSAM embedding (encoder-only, see
  // `services.warm_ai_embedding`) whenever the slice settles while an AI
  // tool is active, *or* when switching into one on the current slice —
  // fire-and-forget, same ~100ms coalescing as the slice load above so
  // scrubbing quickly doesn't fire a warm request per intermediate index.
  // Also opportunistically warms the two neighboring slices (Cellable's
  // `pre_compute_tiff_sam_feature.py` background-fills nearby slices too) —
  // best-effort only, failures ignored, aborted the same way on cleanup.
  useEffect(() => {
    const trackAiTool = trackPromptTool === "box" || trackPromptTool === "point";
    if ((!AI_PREVIEW_TOOLS.includes(paintTool) && !trackAiTool) || !meta.data) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      warmEmbedding(taskId, axis, index, controller.signal).catch(() => {});
      if (index > 0) warmEmbedding(taskId, axis, index - 1, controller.signal).catch(() => {});
      if (index < axisLen - 1) warmEmbedding(taskId, axis, index + 1, controller.signal).catch(() => {});
    }, 100);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [taskId, index, axis, paintTool, trackPromptTool, axisLen, meta.data]);

  useEffect(() => {
    renderOverlay();
  }, [renderOverlay]);

  const markDirty = useCallback(() => {
    dirtyRef.current = true;
    currentSliceDirtyRef.current = true;
    setDirty(true);
    setStatus("dirty");
    const ids = idsRef.current;
    const z = idsIndexRef.current;
    // Keep a live ref while still editing this z; `stashCurrentSlice` freezes
    // it before the canvas buffer is reused for another slice.
    if (ids && z != null) {
      pendingSlicesRef.current.markChanged(z, ids);
    }
    // Keep New ahead of any id the user just painted / committed.
    const aid = activeIdRef.current;
    if (aid >= nextIdRef.current) nextIdRef.current = aid + 1;
  }, []);

  const rememberCommittedLabel = useCallback((labelId: number, layer: number) => {
    rememberLabelLayer(interpLayerMemoryRef.current, axisRef.current, labelId, layer);
  }, []);

  /** Re-derive the dirty flags from what is still pending. */
  const syncDirtyFromPending = useCallback(() => {
    const remaining = pendingSlicesRef.current.size;
    const z = idsIndexRef.current;
    currentSliceDirtyRef.current = z != null && pendingSlicesRef.current.has(z);
    dirtyRef.current = remaining > 0;
    setDirty(remaining > 0);
    return remaining;
  }, []);

  /**
   * Write every pending slice to the working mask. Only an explicit Save click
   * calls this — annotate tools leave edits in memory until then.
   */
  const saveLabels = useCallback(
    async (origin: "manual" | "ai" = "manual"): Promise<boolean> => {
      // A second click while the first request is still running joins it
      // instead of racing it into a duplicate write of the same plane.
      if (saveInFlightRef.current) return saveInFlightRef.current;
      const [h, w] = shapeRef.current;
      if (h === 0 || w === 0) return false;
      // Stash the loaded slice (by its real z) before flushing the pending set.
      stashCurrentSlice();
      if (pendingSlicesRef.current.size === 0) return true;

      const saveAxis = axisRef.current;
      // Snapshots are immutable, so a stroke made while the request is in
      // flight gets a newer revision and stays pending for the next Save
      // rather than being acknowledged by this one.
      const snapshots = pendingSlicesRef.current.snapshots();
      // Saving with Region only on still protects everything outside the ROI
      // on disk — that guard is unchanged. What must not happen is doing it
      // silently now that those edits are visible: the annotator can see the
      // paint, so it has to be their decision whether it is written.
      if (roiOnlyRef.current) {
        // Count what would actually be lost, not what was ever recorded — an
        // annotator who undid their outside strokes should not be warned.
        const outside = snapshots.reduce(
          (total, snapshot) =>
            total +
            (outsideEditsRef.current.peek(snapshot.index)?.pendingCount(snapshot.ids) ?? 0),
          0,
        );
        if (
          outside > 0 &&
          !window.confirm(
            `Region only is on, so ${outside} voxel${outside === 1 ? "" : "s"} you painted ` +
              "outside the region will not be written to disk.\n\n" +
              "Switch Region only off first to save that work too.\n\n" +
              "Save just the inside-region edits?",
          )
        ) {
          return false;
        }
      }
      const operation = (async () => {
        setStatus("saving");
        try {
          let nextId = nextIdRef.current;
          for (const snapshot of snapshots) {
            // Write what the annotator was shown: with Region only off and
            // "Empty voxels only" selected, an outside edit that landed on an
            // existing label was presented as that label, so that is what gets
            // committed. Identity for every plane with no outside edits.
            const committed =
              outsideEditsRef.current.peek(snapshot.index)?.project(snapshot.ids, {
                regionOnly: roiOnlyRef.current,
                overwriteMode: regionOverwriteModeRef.current,
              }) ?? snapshot.ids;
            const runs = encodeRuns(committed as unknown as Uint32Array);
            const res = await putLabelIds(
              taskId,
              saveAxis,
              snapshot.index,
              [h, w],
              runs,
              origin,
              roiOnlyRef.current,
            );
            if (
              pendingSlicesRef.current.acknowledge(
                snapshot.index,
                snapshot.revision,
              )
            ) {
              // This plane is on disk (or, under Region only, its outside part
              // was deliberately left off it). Either way those pixels are no
              // longer pending, so its sparse overwrite baseline goes too.
              outsideEditsRef.current.delete(snapshot.index);
            }
            // What the server now holds for this plane differs from what we
            // cached before the write (and every plane of the other two axes
            // cuts through it — see `invalidateSliceLabelCache`).
            invalidateSliceLabelCache(snapshot.index, saveAxis);
            nextId = res.next_label_id;
          }

          nextIdRef.current = Math.max(nextIdRef.current, nextId);
          const remaining = syncDirtyFromPending();
          // Idle when clean — no ephemeral "Saved" tip in the tool strip.
          setStatus(remaining > 0 ? "dirty" : "idle");
          refreshInstances();
          setLabelsSummaryToken((v) => v + 1);
          return remaining === 0;
        } catch {
          syncDirtyFromPending();
          setStatus("error");
          return false;
        } finally {
          saveInFlightRef.current = null;
        }
      })();
      saveInFlightRef.current = operation;
      return operation;
    },
    [
      taskId,
      refreshInstances,
      stashCurrentSlice,
      invalidateSliceLabelCache,
      syncDirtyFromPending,
    ],
  );

  useEffect(() => {
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!dirtyRef.current && !saveInFlightRef.current) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", warnBeforeUnload);
    };
  }, []);

  // --- "Record hard case" (project-scoped, + optional public link) ---------
  // Two-step by design: recording makes the case visible to everyone on the
  // project, which is not something to do on a stray click, so the button
  // opens a **confirm** and only then creates the case. The public copyable
  // link is offered afterwards as an extra, not as the mechanism.
  const [shareStage, setShareStage] = useState<"confirm" | "done" | null>(null);
  const [shareCase, setShareCase] = useState<HardCase | null>(null);
  const [sharing, setSharing] = useState(false);
  const [shareError, setShareError] = useState<string | null>(null);
  // `copyState` drives the link row's button: it starts on **Copy** and only
  // becomes **Copied** after the user clicks it *and* the clipboard write
  // resolves (03 item D). Recording deliberately does NOT auto-copy — a modal
  // that opens already saying "Copied" is indistinguishable from one that
  // silently failed to copy.
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  const canShareActive = useMemo(
    () => activeId > 0 && labelsSummaryRows.some((r) => r.id === activeId),
    [activeId, labelsSummaryRows],
  );

  /** Step 1 — ask before making this visible to the whole project. */
  const openHardCaseConfirm = useCallback(() => {
    setShareError(null);
    setCopyState("idle");
    setShareCase(null);
    const label = activeIdRef.current;
    // Only record ids that exist as real labels in the volume summary.
    if (!labelsSummaryRows.some((r) => r.id === label)) {
      setShareError(
        `Active id ${label} has no corresponding label — pick an existing label before recording a hard case.`,
      );
      setShareStage("done");
      return;
    }
    setShareStage("confirm");
  }, [labelsSummaryRows]);

  /** Step 2 — actually record it. */
  const confirmHardCase = useCallback(async () => {
    setSharing(true);
    setShareError(null);
    try {
      const created = await createHardCase(taskId, activeIdRef.current);
      setShareCase(created);
      setShareStage("done");
    } catch (e) {
      setShareError(
        e instanceof Error ? e.message : "Could not record this hard case.",
      );
      setShareStage("done");
    } finally {
      setSharing(false);
    }
  }, [taskId]);

  const shareUrl = shareCase ? window.location.origin + shareCase.url : null;

  const copyShareUrl = useCallback(async () => {
    if (!shareUrl) return;
    try {
      // Clipboard can be blocked (insecure context / permission denied) —
      // a rejected write must never read as success.
      await navigator.clipboard.writeText(shareUrl);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  }, [shareUrl]);

  const closeShareModal = useCallback(() => {
    setShareStage(null);
    setShareCase(null);
    setShareError(null);
    setCopyState("idle");
  }, []);

  /** Navigate to another slice — keep unsaved edits in memory until Save. */
  const requestIndex = useCallback((next: number) => {
    const clamped = Math.max(0, Math.min(axisLen - 1, next));
    if (clamped === index) return;
    // A post-click navigation can otherwise display a cached pre-save plane
    // while the PUT is still in flight. Wait for Save to finish so cache
    // invalidation and the next read have a strict order.
    if (saveInFlightRef.current) return;
    // No response computed for the old plane may install a preview after the
    // new plane loads. Live point predicts are intentionally not individually
    // abortable, so the sequence bump is the authoritative stale-response
    // guard; the controller covers Box and committed-point requests.
    aiSeqRef.current += 1;
    aiAbortRef.current?.abort();
    livePredictRef.current = { inFlight: false, dirty: false };
    aiPointsRef.current = [];
    aiPreviewRef.current = null;
    aiTipRef.current = null;
    setHasAiPreview(false);
    setAiPointCount(0);
    // Finish or discard an in-progress brush stroke before parking history,
    // otherwise the baseline would be lost and the stroke wouldn't undo.
    if (drawingRef.current) {
      drawingRef.current = false;
      lastPointRef.current = null;
      const ids = idsRef.current;
      if (ids && historyRef.current.hasOpenStroke) {
        if (historyRef.current.commitStroke(ids)) {
          syncHistoryCounts();
          markDirty();
          if (paintTool === "brush" && ids.some((value) => value === activeIdRef.current)) {
            rememberCommittedLabel(activeIdRef.current, indexRef.current);
          }
        } else {
          historyRef.current.cancelStroke();
        }
      }
    }
    const z = idsIndexRef.current;
    if (currentSliceDirtyRef.current) {
      stashCurrentSlice();
    } else if (z != null) {
      // Keep undo/redo after Save when leaving a clean slice.
      stashHistoryForZ(z);
    }
    setIndex(clamped);
  }, [axisLen, index, stashCurrentSlice, stashHistoryForZ, syncHistoryCounts, markDirty, paintTool, rememberCommittedLabel]);

  /** Make one label Active, following the Labels panel's own selection rule.
   *
   * Clicking a row in **All** jumps to where that label starts; clicking one in
   * **This layer** does not (it is already here). The Select tool / View
   * eyedropper go through the same rule — picking on the canvas and picking in
   * the list share Active + (All-only) layer jump; only the canvas path also
   * pins the Labels row to the top of the list. */
  const selectLabel = useCallback((id: number) => {
    if (id <= 0) return;
    setActiveId(id);
    if (labelsScopeRef.current !== "all") return;
    const row = labelsSummaryRowsRef.current.find((candidate) => candidate.id === id);
    // Nothing to jump to for a label the summary has not seen yet (unsaved
    // paint) — and nothing to do when it already starts on the open layer.
    if (!row || row.z_start === indexRef.current) return;
    requestIndex(row.z_start);
  }, [requestIndex]);

  /** Canvas / View pick: select + scroll that row to the top of Labels. */
  const [pinActiveToTopToken, setPinActiveToTopToken] = useState(0);
  const selectLabelFromCanvas = useCallback((id: number) => {
    selectLabel(id);
    setPinActiveToTopToken((n) => n + 1);
  }, [selectLabel]);

  /** Hard case / public hard-case share: open on a layer where the flagged
   * label actually has voxels.
   *
   * Those pages seed `initialSoloId` + `initialActiveId` with the recorded
   * label, but the opening layer comes from the *task's* `z_start` (usually
   * layer 1) — `HardCase.z_start` is denormalized from the task, not from the
   * label. Solo paints that one label only, so a label living at z 243-256 left
   * the recipient on an empty canvas while the 3D panel happily showed its
   * mesh, and the only way out was clearing the visibility filter by hand.
   *
   * The label's extent is only known once the Labels summary lands, so the jump
   * waits for it and then fires once, on the same `row.z_start` a Labels row
   * click uses. Deliberately conservative: it never fights an axis the viewer
   * has already changed, nor a layer they have already navigated to
   * themselves. The Labels list defaults to **All** (`labelsScope`), so the
   * focused row is listed — and its one-shot scroll fires — even before the
   * jump lands. */
  const focusLabelId = initialSoloId ?? initialActiveId ?? null;
  const focusJumpDoneRef = useRef(false);
  useEffect(() => {
    focusJumpDoneRef.current = false;
  }, [taskId, focusLabelId]);
  useEffect(() => {
    if (focusJumpDoneRef.current) return;
    if (focusLabelId == null || focusLabelId <= 0) return;
    // `axisLength` answers 1 while the volume meta is still in flight, which
    // would clamp any jump down to layer 0 — exactly the bug being fixed.
    if (!meta.data) return;
    if (axis !== DEFAULT_VIEW_AXIS) return;
    if (indexRef.current !== zStart) {
      // They already moved; their layer outranks the default landing.
      focusJumpDoneRef.current = true;
      return;
    }
    const row = labelsSummaryRows.find((candidate) => candidate.id === focusLabelId);
    // A summary that has not seen this id yet may still be mid-refresh, so
    // leave the jump armed rather than stranding the recipient on layer 1.
    if (!row) return;
    focusJumpDoneRef.current = true;
    if (row.z_start !== indexRef.current) {
      requestIndex(row.z_start);
    }
    setPinActiveToTopToken((n) => n + 1);
  }, [focusLabelId, labelsSummaryRows, meta.data, axis, zStart, taskId, requestIndex]);

  /** Switch Axial / Coronal / Sagittal. Must reload even when index stays 0
   * (the old load effect only depended on `index`, so Coronal looked "stuck"
   * on Axial). Discard pending edits on the previous axis — their slice
   * indices mean something else on the new axis. */
  const changeAxis = useCallback(
    (next: Axis) => {
      if (next === axisRef.current) return;
      if (saveInFlightRef.current) {
        window.alert("Please wait for the current save to finish before switching axis.");
        return;
      }
      aiSeqRef.current += 1;
      aiAbortRef.current?.abort();
      livePredictRef.current = { inFlight: false, dirty: false };
      historyRef.current.cancelStroke();
      drawingRef.current = false;
      lastPointRef.current = null;
      if (dirtyRef.current || pendingSlicesRef.current.size > 0) {
        if (
          !window.confirm(
            "Unsaved edits on this axis will be discarded when you switch. Continue?",
          )
        ) {
          return;
        }
      }
      // Slice caches are keyed `axis:index`, so the previous axis's entries
      // are still correct (and make switching back instant) — and a request
      // that was already in flight can't land on the new axis's key either.
      // Pending edits and undo history *are* index-keyed, and an index means
      // a different plane now, so those are what has to go.
      pendingSlicesRef.current.clear();
      // Same reasoning: a plane index means a different plane on the new axis,
      // so its sparse overwrite baselines cannot follow the axis switch.
      outsideEditsRef.current.clear();
      clearAllHistory();
      dirtyRef.current = false;
      currentSliceDirtyRef.current = false;
      setDirty(false);
      setStatus("idle");
      idsIndexRef.current = null;
      idsRef.current = null;
      aiPointsRef.current = [];
      aiPreviewRef.current = null;
      setHasAiPreview(false);
      setAiPointCount(0);
      setAxis(next);
      // Always land on slice 0 of the new axis. Even if index was already 0,
      // the load effect now also keys on `axis`, so the new view fetches.
      setIndex(0);
    },
    [meta.data, clearAllHistory],
  );

  const currentViewLocation = useCallback((): ViewLocation => {
    const [h, w] = shapeRef.current;
    const [row, col] = hoverPosRef.current ?? [Math.floor(h / 2), Math.floor(w / 2)];
    const voxel = voxelFromSlice(axisRef.current, indexRef.current, row, col);
    return {
      ...voxel,
      axis: axisRef.current,
      ...(activeIdRef.current > 0 ? {label: activeIdRef.current} : {}),
    };
  }, []);

  // Publish navigation, position, and the existing Region display/edit gate
  // to the page topbar (View + Annotate).
  useEffect(() => {
    if (!onAxisControls) return;
    onAxisControls({
      axis,
      changeAxis,
      disabled: swapped && editable,
      currentLocation: currentViewLocation,
      hasRegion: Boolean(meta.data?.has_region_mask),
      regionOnly: roiOnly,
      changeRegionOnly: setRoiOnly,
      regionOverwriteMode,
      changeRegionOverwriteMode: setRegionOverwriteMode,
    });
    return () => onAxisControls(null);
  }, [
    onAxisControls,
    axis,
    changeAxis,
    swapped,
    editable,
    currentViewLocation,
    meta.data?.has_region_mask,
    roiOnly,
    regionOverwriteMode,
  ]);

  // Widened past `React.PointerEvent` (structurally: any event with
  // clientX/clientY) so the same conversion serves pointer moves/clicks
  // *and* the right-click context menu (#29 item U15), which fires a
  // `React.MouseEvent`.
  const pixelFromEvent = useCallback((e: { clientX: number; clientY: number }): [number, number] | null => {
    const canvas = overlayRef.current;
    const [h, w] = shapeRef.current;
    if (!canvas || h === 0 || w === 0) return null;
    const rect = canvas.getBoundingClientRect();
    const nx = (e.clientX - rect.left) / rect.width;
    const ny = (e.clientY - rect.top) / rect.height;
    if (nx < 0 || nx > 1 || ny < 0 || ny > 1) return null;
    return [Math.floor(ny * h), Math.floor(nx * w)];
  }, []);

  // Index into `aiPointsRef` of the committed point nearest an image-space
  // click, if any is within `toleranceScreenPx` on-screen pixels — shared by
  // Alt+click-to-remove (#25 item E) and drag-to-move (#29 item U8) so both
  // "clicked on an existing point" checks use the exact same hit-test.
  const nearestCommittedPointIndex = useCallback((px: number, py: number, toleranceScreenPx: number): number => {
    const pts = aiPointsRef.current;
    if (pts.length === 0) return -1;
    const canvas = overlayRef.current;
    const [, w] = shapeRef.current;
    const rect = canvas?.getBoundingClientRect();
    const scale = rect && rect.width > 0 ? rect.width / w : 1;
    const tolerance = toleranceScreenPx / Math.max(scale, 0.001);
    let nearestIdx = -1;
    let nearestDist = Infinity;
    pts.forEach((p, i) => {
      const d = Math.hypot(p.x - px, p.y - py);
      if (d < nearestDist) {
        nearestDist = d;
        nearestIdx = i;
      }
    });
    return nearestIdx >= 0 && nearestDist <= tolerance ? nearestIdx : -1;
  }, []);

  const paintAt = useCallback(
    (py: number, px: number, value: number, target: Int32Array, size: number) => {
      const [h, w] = shapeRef.current;
      // `size` is the footprint's width in pixels, not its radius. Size 1 is
      // therefore exactly the pixel under the cursor — the old reading (radius)
      // made the smallest possible brush a five-pixel plus, which is why
      // single-voxel corrections were impossible.
      const radius = brushRadius(size);
      const reach = Math.floor(radius);
      const y0 = Math.max(0, py - reach);
      const y1 = Math.min(h - 1, py + reach);
      const x0 = Math.max(0, px - reach);
      const x1 = Math.min(w - 1, px + reach);
      const r2 = radius * radius;
      const roi =
        roiOnlyRef.current && regionMaskBitsRef.current?.mask.length === target.length
          ? regionMaskBitsRef.current.mask
          : null;
      // Never edit through a label the strict Region-only filter hides. If
      // the ROI is still decoding, fail closed for paint instead of risking a
      // destructive invisible stroke.
      if (roiOnlyRef.current && !roi) return;
      const touching = roi ? regionTouchingIdsRef.current : null;
      if (roi && !touching) return;
      const z = idsIndexRef.current;
      const outside = roi && z != null ? outsideEditsRef.current.for(z) : null;
      const baseline = baselineIdsRef.current;
      let recorded = 0;
      for (let y = y0; y <= y1; y++) {
        const dy = y - py;
        for (let x = x0; x <= x1; x++) {
          const dx = x - px;
          if (dx * dx + dy * dy > r2) continue;
          const at = y * w + x;
          const prior = target[at];
          if (touching && prior > 0 && !touching.has(prior)) continue;
          if (outside && roi![at] === 0 && !outside.has(at)) {
            // Baseline is what the *server* holds, not what is on screen: a
            // pixel painted twice must still remember the stored value.
            outside.record(at, baseline && baseline.length === target.length ? baseline[at] : 0);
            recorded += 1;
          }
          target[at] = value;
          if (touching && roi![at] !== 0 && value > 0) touching.add(value);
        }
      }
      if (recorded > 0) setOutsideEditRevision((v) => v + 1);
    },
    [],
  );

  /**
   * Record outside-region pixels for a tool that rewrites a plane wholesale
   * (flood fill, box erase) rather than stamping through `paintAt`.
   *
   * Only the plane on screen can be recorded: its ROI is the one decoded here.
   * A 3-D flood reaching other planes leaves those unrecorded, so they keep the
   * pre-existing behaviour — Region only's ROI guard still protects them on
   * disk. Depth 1 (the default) is the fully covered case.
   */
  const recordOutsideChanges = useCallback(
    (planeIndex: number, before: Int32Array, after: Int32Array) => {
      if (!roiOnlyRef.current || planeIndex !== idsIndexRef.current) return;
      const roi = regionMaskBitsRef.current?.mask;
      if (!roi || roi.length !== after.length) return;
      const edits = outsideEditsRef.current.for(planeIndex);
      // Same baseline `paintAt` uses: what the *server* holds, so "empty
      // voxels only" means "do not replace a stored label" no matter which
      // tool made the edit.
      const stored =
        baselineIdsRef.current?.length === after.length ? baselineIdsRef.current : before;
      let recorded = 0;
      for (let i = 0; i < after.length; i++) {
        if (roi[i] !== 0 || before[i] === after[i] || edits.has(i)) continue;
        edits.record(i, stored[i]);
        recorded += 1;
      }
      if (recorded > 0) setOutsideEditRevision((v) => v + 1);
    },
    [],
  );

  /** Who Region only shows on *one arbitrary plane* — the volume-wide
   * membership unioned with what that plane itself reveals.
   *
   * The bulk tools (Interpolate, Flood fill, and every server plan) protect
   * planes other than the one on screen, so they cannot read
   * `regionTouchingIdsRef`, which tracks the visible plane. Deciding from the
   * plane alone would re-protect exactly the labels Region only is now showing
   * whole, i.e. silently revert strokes on visible instances. */
  const regionVisibleIdsFor = useCallback(
    (ids: Int32Array, region: Uint8Array): ReadonlySet<number> =>
      regionMembership(roiVolumeIdsRef.current, labelIdsTouchingRegion(ids, region))
      ?? new Set<number>(),
    [],
  );

  const strokeTo = useCallback(
    (py: number, px: number) => {
      const ids = idsRef.current;
      const last = lastPointRef.current;
      const steps = last ? Math.max(1, Math.ceil(Math.hypot(py - last[0], px - last[1]))) : 1;
      if (ids) {
        for (let s = 0; s <= steps; s++) {
          const t = steps === 0 ? 1 : s / steps;
          const y = last ? Math.round(last[0] + (py - last[0]) * t) : py;
          const x = last ? Math.round(last[1] + (px - last[1]) * t) : px;
          if (paintTool === "brush") paintAt(y, x, activeId, ids, brushSize);
          else if (paintTool === "eraser") paintAt(y, x, 0, ids, eraserSize);
        }
      }
      lastPointRef.current = [py, px];
      renderOverlay();
    },
    [paintTool, activeId, brushSize, eraserSize, paintAt, renderOverlay],
  );

  // --- Point Mask / Boundary: accumulate prompt points, live-predict ------
  //
  // Every predict call is sequence-guarded: a rapid extra click (or a new
  // box drag) starts a new sequence number and aborts whatever was still
  // in flight, and any response — success, failure, or an aborted-request
  // exception — is only applied if its sequence number is still current.
  // This is what keeps overlapping predicts from corrupting each other
  // (Cellable's own predicts are effectively serialized by the Qt event
  // loop calling `_finaliseImpl`/paintEvent one at a time; a browser has no
  // such guarantee once two `fetch`es are in flight together).

  // Shared core: predict from an explicit point set. Used two ways —
  // committed-only (`runPredictPoints`, the click/Alt-click/finalize path)
  // and committed∪{live cursor tip} (`scheduleLivePredict`, #27's
  // cursor-follow). Both share one `aiSeqRef`/`aiAbortRef` pair so whichever
  // call is most recent always wins regardless of which path fired it —
  // a click predict racing a hover predict can't corrupt either's result.
  const runPredictPointsWith = useCallback(
    async (pts: AiPoint[], opts?: { silent?: boolean; live?: boolean }) => {
      const silent = opts?.silent === true;
      const live = opts?.live === true;
      // Click/finalize always cancel whatever was in flight. Live coalesced
      // predicts do not abort each other — that was why the mask felt frozen
      // (every move killed the in-flight request; only a stale last-good fill
      // remained on screen).
      if (!live) {
        aiAbortRef.current?.abort();
        livePredictRef.current = { inFlight: false, dirty: false };
      }
      if (pts.length === 0) {
        aiPreviewRef.current = null;
        setHasAiPreview(false);
        renderOverlay();
        return;
      }
      const controller = new AbortController();
      if (!live) aiAbortRef.current = controller;
      const seq = ++aiSeqRef.current;
      const requestAxis = axisRef.current;
      const requestIndex = indexRef.current;
      const points: [number, number][] = pts.map((p) => [p.x, p.y]);
      const pointLabels = pts.map((p) => p.label);
      if (!silent) {
        setAiError(null);
      }
      try {
        const res =
          paintTool === "boundary"
            ? await predictBoundary(taskId, requestAxis, requestIndex, points, pointLabels, live ? undefined : controller.signal, roiOnlyRef.current)
            : await predictMaskFromPoints(taskId, requestAxis, requestIndex, points, pointLabels, live ? undefined : controller.signal, roiOnlyRef.current);
        if (
          axisRef.current !== requestAxis ||
          indexRef.current !== requestIndex
        ) {
          return;
        }
        if (aiSeqRef.current !== seq && !live) return;
        // Live path: apply if this is still the latest live seq OR no newer
        // click bumped the counter past us mid-flight.
        if (live && aiSeqRef.current !== seq) {
          // A click/clear happened — discard.
          return;
        }
        const [h, w] = res.shape;
        const mask = decodeRuns(res.runs, h * w);
        if (!mask.some((v) => v !== 0)) {
          if (!live) {
            aiPreviewRef.current = null;
            setHasAiPreview(false);
            setAiError("No mask found for these points — try adding another point.");
          }
          return;
        }
        aiPreviewRef.current = {
          mask: Uint8Array.from(mask),
          shape: [h, w],
          axis: requestAxis,
          index: requestIndex,
        };
        setHasAiPreview(true);
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        if (aiSeqRef.current !== seq) return;
        if (!silent) setAiError(e instanceof Error ? e.message : "Prediction failed");
      } finally {
        renderOverlay();
        if (live) {
          const st = livePredictRef.current;
          st.inFlight = false;
          if (st.dirty) {
            st.dirty = false;
            const committed = aiPointsRef.current;
            if (committed.length > 0) {
              const tip = aiTipRef.current;
              st.inFlight = true;
              void runPredictPointsWithRef.current(tip ? [...committed, tip] : committed, {
                silent: true,
                live: true,
              });
            }
          }
        }
      }
    },
    [taskId, paintTool, renderOverlay],
  );
  runPredictPointsWithRef.current = runPredictPointsWith;

  // Committed-only predict (no cursor tip) — click / Alt-click / Enter path.
  const runPredictPoints = useCallback(
    () => runPredictPointsWith(aiPointsRef.current),
    [runPredictPointsWith],
  );

  // Cursor-follow: coalesce to latest tip (Cellable paintEvent feel over HTTP).
  const scheduleLivePredict = useCallback(() => {
    const pts = aiPointsRef.current;
    if (pts.length === 0) return;
    const st = livePredictRef.current;
    if (st.inFlight) {
      st.dirty = true;
      return;
    }
    st.inFlight = true;
    st.dirty = false;
    const tip = aiTipRef.current;
    void runPredictPointsWith(tip ? [...pts, tip] : pts, { silent: true, live: true });
  }, [runPredictPointsWith]);

  const runPredictBox = useCallback(
    async (box: [[number, number], [number, number]]) => {
      aiAbortRef.current?.abort();
      const controller = new AbortController();
      aiAbortRef.current = controller;
      const seq = ++aiSeqRef.current;
      const requestAxis = axisRef.current;
      const requestIndex = indexRef.current;
      setAiError(null);
      try {
        const res = await predictMaskFromBox(
          taskId,
          requestAxis,
          requestIndex,
          box,
          controller.signal,
          roiOnlyRef.current,
        );
        if (
          axisRef.current !== requestAxis ||
          indexRef.current !== requestIndex
        ) {
          return;
        }
        if (aiSeqRef.current !== seq) return;
        const [h, w] = res.shape;
        const mask = decodeRuns(res.runs, h * w);
        if (!mask.some((v) => v !== 0)) {
          aiPreviewRef.current = null;
          setHasAiPreview(false);
          // Nothing to commit, so a pending double-click intent must not
          // survive to fire against some later prediction.
          finalizeBoxWhenReadyRef.current = false;
          setAiError("No mask found for this box — try a tighter/looser box.");
          return;
        }
        aiPreviewRef.current = {
          mask: Uint8Array.from(mask),
          shape: [h, w],
          axis: requestAxis,
          index: requestIndex,
        };
        setHasAiPreview(true);
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        if (aiSeqRef.current !== seq) return;
        // A failed prediction has no proposal to commit; drop the deferred
        // double-click intent rather than letting it apply to a later one.
        finalizeBoxWhenReadyRef.current = false;
        setAiError(e instanceof Error ? e.message : "Prediction failed");
      } finally {
        renderOverlay();
      }
    },
    [taskId, renderOverlay],
  );

  const commitAiPreview = useCallback(() => {
    // Re-entrancy guard (Cellable's `_finaliseInProgress`): a double
    // Enter/click while this is running must not double-apply the same
    // preview or double-fire the commit PUT.
    if (committingAiRef.current) return;
    const preview = aiPreviewRef.current;
    const ids = idsRef.current;
    if (!preview || !ids) return;
    // A stale preview from another plane must never paint into this buffer.
    if (
      preview.axis !== axisRef.current ||
      preview.index !== indexRef.current ||
      idsIndexRef.current !== preview.index ||
      preview.shape[0] * preview.shape[1] !== ids.length
    ) {
      aiPreviewRef.current = null;
      setHasAiPreview(false);
      return;
    }
    committingAiRef.current = true;
    try {
      const before = roiOnlyRef.current ? ids.slice() : null;
      const changed = historyRef.current.withChange(ids, () => {
        const region = roiOnlyRef.current ? regionMaskBitsRef.current?.mask : null;
        const visible = region?.length === ids.length
          ? labelIdsTouchingRegion(ids, region)
          : null;
        // Fail closed until the current ROI is decoded. A proposal can remain
        // visible, but committing it must never paint through hidden labels.
        if (roiOnlyRef.current && !visible) return;
        for (let i = 0; i < ids.length; i++) {
          if (!preview.mask[i]) continue;
          const prior = ids[i];
          if (visible && prior > 0 && !visible.has(prior)) continue;
          ids[i] = activeId;
        }
      });
      aiPreviewRef.current = null;
      aiPointsRef.current = [];
      aiTipRef.current = null;
      aiSeqRef.current += 1; // drop any predict still in flight for this prompt
      aiAbortRef.current?.abort();
      setHasAiPreview(false);
      setAiPointCount(0);
      renderOverlay();
      if (!changed) return;
      if (before && idsIndexRef.current != null) {
        recordOutsideChanges(idsIndexRef.current, before, ids);
      }
      refreshInstances();
      syncHistoryCounts();
      markDirty();
      rememberCommittedLabel(activeId, preview.index);
    } finally {
      committingAiRef.current = false;
    }
  }, [activeId, recordOutsideChanges, renderOverlay, refreshInstances, syncHistoryCounts, markDirty, rememberCommittedLabel]);

  useEffect(() => {
    if (
      !finalizeBoxWhenReadyRef.current ||
      paintTool !== "box_mask" ||
      !hasAiPreview
    ) {
      return;
    }
    finalizeBoxWhenReadyRef.current = false;
    commitAiPreview();
  }, [paintTool, hasAiPreview, commitAiPreview]);

  // Enter/Ctrl-click/double-click finalize: match Cellable exactly — a
  // fresh committed-only predict, THEN commit that (not whatever the last
  // hover frame happened to show) — see #27 item L4. No-ops with no
  // committed points; `commitAiPreview` itself no-ops if the fresh predict
  // came back empty.
  const finalizeAiPoints = useCallback(async () => {
    if (aiPointsRef.current.length === 0) return;
    const axisAtStart = axisRef.current;
    const indexAtStart = indexRef.current;
    await runPredictPoints();
    if (
      axisRef.current !== axisAtStart ||
      indexRef.current !== indexAtStart ||
      !aiPreviewRef.current ||
      aiPreviewRef.current.axis !== axisAtStart ||
      aiPreviewRef.current.index !== indexAtStart
    ) {
      return;
    }
    commitAiPreview();
  }, [runPredictPoints, commitAiPreview]);

  /** Commit or discard an open brush/erase stroke. Idempotent. */
  const finishOpenStroke = useCallback(() => {
    if (!drawingRef.current && !historyRef.current.hasOpenStroke) return false;
    drawingRef.current = false;
    lastPointRef.current = null;
    const ids = idsRef.current;
    if (ids && historyRef.current.hasOpenStroke) {
      if (historyRef.current.commitStroke(ids)) {
        syncHistoryCounts();
        markDirty();
        if (paintTool === "brush" && ids.some((value) => value === activeIdRef.current)) {
          rememberCommittedLabel(activeIdRef.current, indexRef.current);
        }
        return true;
      }
      historyRef.current.cancelStroke();
      syncHistoryCounts();
      return false;
    }
    return false;
  }, [syncHistoryCounts, markDirty, paintTool, rememberCommittedLabel]);

  const clearAiPoints = useCallback(() => {
    // Preserve any in-progress brush/erase pixels before clearing AI state —
    // this function also runs on every tool switch.
    finishOpenStroke();
    aiAbortRef.current?.abort();
    aiSeqRef.current += 1;
    aiPointsRef.current = [];
    aiPreviewRef.current = null;
    aiTipRef.current = null;
    finalizeBoxWhenReadyRef.current = false;
    livePredictRef.current = { inFlight: false, dirty: false };
    draggingPointIdxRef.current = null;
    // Also cancels an in-progress Box Mask rubber-band — Escape must clear
    // Box the same as Point/Boundary (#25 item C.2/C.5), and this is also
    // the function the "leave an AI tool" effect below calls.
    boxDragRef.current = null;
    drawingRef.current = false;
    setHasAiPreview(false);
    setAiPointCount(0);
    setAiError(null);
    renderOverlay();
  }, [renderOverlay, finishOpenStroke]);

  // Leaving an AI tool (Point/Box/Boundary) for any other tool clears its
  // proposal/points and aborts an in-flight predict; entering a (possibly
  // different) AI tool always starts clean (#25 item H).
  useEffect(() => {
    if (prevPaintToolRef.current === paintTool) return;
    prevPaintToolRef.current = paintTool;
    clearAiPoints();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paintTool]);

  // Leaving Interpolate clears endpoint markers; axis switch does too
  // (a slice index means a different plane on another axis).
  useEffect(() => {
    if (paintTool === "interpolate") return;
    interpAnchorRef.current = null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paintTool]);

  const prevInterpolateToolRef = useRef<PaintTool>(paintTool);
  useEffect(() => {
    const entering = prevInterpolateToolRef.current !== "interpolate" && paintTool === "interpolate";
    prevInterpolateToolRef.current = paintTool;
    if (!entering) return;
    if (interpContextSelectionRef.current) {
      interpContextSelectionRef.current = false;
      return;
    }
    const pair = rememberedNonAdjacentPair(
      interpLayerMemoryRef.current,
      axisRef.current,
      activeIdRef.current,
    );
    if (!pair) return;
    setInterpFirst(pair[0]);
    setInterpLast(pair[1]);
    interpAnchorRef.current = null;
  }, [paintTool]);

  const prevMergeToolRef = useRef(paintTool);
  useEffect(() => {
    if (prevMergeToolRef.current !== paintTool && paintTool === "merge") {
      mergeClickSlotRef.current = mergeClickSlotForInputs(mergeIdA, mergeIdB);
    }
    prevMergeToolRef.current = paintTool;
  }, [paintTool, mergeIdA, mergeIdB]);

  useEffect(() => {
    setInterpFirst(null);
    setInterpLast(null);
    interpAnchorRef.current = null;
  }, [axis]);

  const applyBoxErase = useCallback(
    (y0: number, y1: number, x0: number, x1: number) => {
      const ids = idsRef.current;
      const [h, w] = shapeRef.current;
      if (!ids) return;
      const before = roiOnlyRef.current ? ids.slice() : null;
      const changed = historyRef.current.withChange(ids, () => {
        const region = roiOnlyRef.current ? regionMaskBitsRef.current?.mask : null;
        const visible = region?.length === ids.length
          ? labelIdsTouchingRegion(ids, region)
          : null;
        if (roiOnlyRef.current && !visible) return;
        const cy1 = Math.min(h - 1, y1);
        const cx1 = Math.min(w - 1, x1);
        for (let y = Math.max(0, y0); y <= cy1; y++) {
          for (let x = Math.max(0, x0); x <= cx1; x++) {
            const at = y * w + x;
            if (visible && ids[at] > 0 && !visible.has(ids[at])) continue;
            ids[at] = 0;
          }
        }
      });
      if (!changed) return;
      if (before && idsIndexRef.current != null) {
        recordOutsideChanges(idsIndexRef.current, before, ids);
      }
      renderOverlay();
      refreshInstances();
      syncHistoryCounts();
      markDirty();
    },
    [renderOverlay, refreshInstances, syncHistoryCounts, markDirty, recordOutsideChanges],
  );

  /** Interpolate between endpoints using the latest in-memory planes
   * (including unsaved pending edits). Fully client-side — no Save required,
   * no server plan. Writes into the pending buffer; Undo/Save as usual. */
  const runInterpolation = useCallback(async () => {
    if (interpRunning || interpFirst == null || interpLast == null) return;
    if (activeId < 1) return;
    if (Math.abs(interpFirst - interpLast) < 2) return;
    setInterpRunning(true);
    try {
      const plannedAxis = axisRef.current;
      const [h, w] = shapeRef.current;
      const liveZ = idsIndexRef.current;
      if (liveZ == null || h === 0 || w === 0) return;

      const loadPlane = async (zi: number): Promise<Int32Array | null> => {
        if (zi === liveZ && idsRef.current) return idsRef.current.slice();
        const cached = pendingSlicesRef.current.get(zi);
        if (cached && cached.length === h * w) return cached.slice();
        const resp = await labelRunsFor(zi);
        if (idsIndexRef.current !== indexRef.current) return null;
        return decodeRuns(resp.runs, h * w);
      };

      const lo = Math.min(interpFirst, interpLast);
      const hi = Math.max(interpFirst, interpLast);
      const firstPlane = await loadPlane(lo);
      const lastPlane = await loadPlane(hi);
      if (!firstPlane || !lastPlane) return;
      if (axisRef.current !== plannedAxis) return;

      const planned = await planLocalInterpolationAsync(
        firstPlane,
        lastPlane,
        h,
        w,
        lo,
        hi,
        activeId,
      );
      // Scrubbing is intentionally still responsive while the worker runs.
      // A completed plan belongs to the exact plane/view that launched it;
      // discard it instead of applying old endpoints into a newly loaded idsRef.
      if (
        axisRef.current !== plannedAxis ||
        idsIndexRef.current !== liveZ ||
        indexRef.current !== liveZ
      ) return;
      if (planned.length === 0) return;

      const label = activeId;
      const emptyOnly = overwriteMode === "overwrite_empty";

      const compound: { index: number; before: Int32Array; after: Int32Array }[] =
        [];
      for (let plannedOffset = 0; plannedOffset < planned.length; plannedOffset++) {
        const entry = planned[plannedOffset];
        const zi = entry.index;
        const mask = entry.mask;
        let before: Int32Array;
        if (zi === liveZ && idsRef.current) {
          before = idsRef.current.slice();
        } else {
          const cached = pendingSlicesRef.current.get(zi);
          if (cached && cached.length === h * w) {
            before = cached.slice();
          } else {
            const resp = await labelRunsFor(zi);
            if (idsIndexRef.current !== indexRef.current) return;
            before = decodeRuns(resp.runs, h * w);
          }
        }
        const after = before.slice();
        let changed = false;
        for (let i = 0; i < mask.length; i++) {
          if (!mask[i]) continue;
          if (emptyOnly && after[i] !== 0) continue;
          if (after[i] !== label) {
            after[i] = label;
            changed = true;
          }
        }
        if (roiOnlyRef.current) {
          const regionUrl = await sliceRegionUrl(zi, undefined, plannedAxis);
          if (!regionUrl) return;
          const region = await decodeRegionMask(regionUrl, w, h);
          // Same membership the overlay draws with, so a label visible
          // here because it reaches the ROI elsewhere is editable here.
          protectHiddenRegionLabels(before, after, region, regionVisibleIdsFor(before, region));
          changed = !before.every((value, offset) => value === after[offset]);
        }
        if (!changed) continue;
        compound.push({ index: zi, before, after });
        rememberCommittedLabel(label, zi);
        if (zi === liveZ && idsRef.current) {
          idsRef.current.set(after);
          pendingSlicesRef.current.markChanged(zi, idsRef.current);
        } else {
          pendingSlicesRef.current.markChanged(zi, after);
        }
        if (plannedOffset % 2 === 1) await yieldToMainThread();
      }
      if (!historyRef.current.recordCompound(compound)) return;

      dirtyRef.current = true;
      currentSliceDirtyRef.current = pendingSlicesRef.current.has(liveZ);
      setDirty(true);
      setStatus("dirty");
      const aid = activeIdRef.current;
      if (aid >= nextIdRef.current) nextIdRef.current = aid + 1;

      interpPreviewRef.current = null;
      setInterpPreviewCount(0);

      const mid = Math.floor((lo + hi) / 2);
      if (mid !== liveZ) {
        requestIndex(mid);
      } else {
        renderOverlay();
      }
      refreshInstances();
      syncHistoryCounts();
    } catch {
      // Silent — no dialogs.
    } finally {
      setInterpRunning(false);
    }
  }, [
    activeId,
    interpFirst,
    interpLast,
    interpRunning,
    labelRunsFor,
    overwriteMode,
    refreshInstances,
    renderOverlay,
    requestIndex,
    rememberCommittedLabel,
    sliceRegionUrl,
    syncHistoryCounts,
  ]);

  const runFloodFill = useCallback(async (row: number, col: number) => {
    if (floodRunning || activeId < 1) return;
    const ids = idsRef.current;
    const z = idsIndexRef.current;
    const [h, w] = shapeRef.current;
    if (!ids || z == null || h === 0 || w === 0) return;
    if (idsIndexRef.current !== indexRef.current) return;

    setFloodRunning(true);
    try {
      const plannedAxis = axisRef.current;
      const depth = plannedAxis === "z" ? Math.max(1, floodDepth) : 1;

      let z0 = z;
      let z1 = z + 1;
      if (depth > 1) {
        z0 = Math.max(0, z - Math.floor(depth / 2));
        z1 = Math.min(axisLen, z0 + depth);
        z0 = Math.max(0, z1 - depth);
      }
      const d = z1 - z0;
      if (d < 1) return;

      const befores = new Map<number, Int32Array>();
      const block = new Int32Array(d * h * w);
      for (let zi = z0; zi < z1; zi++) {
        let plane: Int32Array;
        if (zi === z) {
          plane = ids.slice();
        } else {
          const cached = pendingSlicesRef.current.get(zi);
          if (cached && cached.length === h * w) {
            plane = cached.slice();
          } else {
            const resp = await labelRunsFor(zi);
            if (idsIndexRef.current !== indexRef.current) return;
            plane = decodeRuns(resp.runs, h * w);
          }
        }
        befores.set(zi, plane.slice());
        block.set(plane, (zi - z0) * h * w);
      }

      const voxels = floodFillBlock(
        block,
        d,
        h,
        w,
        z - z0,
        row,
        col,
        activeId,
        overwriteMode,
      );
      if (voxels === 0) return;

      const liveZ = z;
      const compound: { index: number; before: Int32Array; after: Int32Array }[] =
        [];
      for (let offset = 0; offset < d; offset++) {
        const zi = z0 + offset;
        const after = block.slice(offset * h * w, (offset + 1) * h * w);
        const before = befores.get(zi)!;
        if (roiOnlyRef.current) {
          const regionUrl = await sliceRegionUrl(zi, undefined, plannedAxis);
          if (!regionUrl) return;
          const region = await decodeRegionMask(regionUrl, w, h);
          // Same membership the overlay draws with, so a label visible
          // here because it reaches the ROI elsewhere is editable here.
          protectHiddenRegionLabels(before, after, region, regionVisibleIdsFor(before, region));
        }
        if (before.every((v, i) => v === after[i])) continue;
        compound.push({ index: zi, before, after });
        if (after.some((value) => value === activeId)) {
          rememberCommittedLabel(activeId, zi);
        }
        if (zi === liveZ) {
          recordOutsideChanges(zi, before, after);
          ids.set(after);
          pendingSlicesRef.current.markChanged(zi, ids);
        } else {
          pendingSlicesRef.current.markChanged(zi, after);
        }
      }
      if (!historyRef.current.recordCompound(compound)) return;

      dirtyRef.current = true;
      currentSliceDirtyRef.current = pendingSlicesRef.current.has(liveZ);
      setDirty(true);
      setStatus("dirty");
      const aid = activeIdRef.current;
      if (aid >= nextIdRef.current) nextIdRef.current = aid + 1;

      renderOverlay();
      refreshInstances();
      syncHistoryCounts();
    } catch {
      // Silent — flood fill stays local; no dialog on empty/failed fills.
    } finally {
      setFloodRunning(false);
    }
  }, [
    activeId,
    axisLen,
    floodDepth,
    floodRunning,
    labelRunsFor,
    markDirty,
    overwriteMode,
    refreshInstances,
    renderOverlay,
    recordOutsideChanges,
    rememberCommittedLabel,
    sliceRegionUrl,
    syncHistoryCounts,
  ]);

  const pendingToolSlices = useCallback((): PendingToolSlice[] => {
    const [h, w] = shapeRef.current;
    return pendingSlicesRef.current.snapshots().map((snapshot) => ({
      index: snapshot.index,
      shape: [h, w],
      runs: encodeRuns(snapshot.ids as unknown as Uint32Array),
    }));
  }, []);

  /** Write one already-recorded compound into the live buffer + pending slices.
   * Split out of `applyPendingToolPlan` so Track's Reject can push the inverse
   * of a propagation through the identical path. */
  const writeCompoundEdits = useCallback((
    compound: CompoundSliceEdit[],
    liveIndex: number | null,
    maxLabel: number,
  ) => {
    for (const edit of compound) {
      if (edit.index === liveIndex && idsRef.current) {
        recordOutsideChanges(edit.index, edit.before, edit.after);
        idsRef.current.set(edit.after);
        pendingSlicesRef.current.markChanged(edit.index, idsRef.current);
      } else {
        pendingSlicesRef.current.markChanged(edit.index, edit.after);
      }
    }
    nextIdRef.current = Math.max(nextIdRef.current, maxLabel + 1);
    dirtyRef.current = true;
    currentSliceDirtyRef.current = liveIndex != null && pendingSlicesRef.current.has(liveIndex);
    setDirty(true);
    setStatus("dirty");
    renderOverlay();
    refreshInstances();
    syncHistoryCounts();
  }, [recordOutsideChanges, refreshInstances, renderOverlay, syncHistoryCounts]);

  /** Apply server-computed planes to the same pending compound history used by
   * Interpolate/Flood. The server response is a plan only; Save remains the
   * sole label-volume persistence path. */
  const applyPendingToolPlan = useCallback(async (
    plannedAxis: Axis,
    slices: PlannedLabelSlice[],
  ): Promise<boolean> => {
    if (plannedAxis !== axisRef.current || idsIndexRef.current !== indexRef.current) return false;
    const liveIndex = idsIndexRef.current;
    const compound: { index: number; before: Int32Array; after: Int32Array }[] = [];
    let maxLabel = 0;
    for (let sliceOffset = 0; sliceOffset < slices.length; sliceOffset++) {
      if (plannedAxis !== axisRef.current || idsIndexRef.current !== indexRef.current) {
        return false;
      }
      const slice = slices[sliceOffset];
      const [h, w] = slice.shape;
      let before: Int32Array;
      if (slice.index === liveIndex && idsRef.current) {
        before = idsRef.current.slice();
      } else {
        const pending = pendingSlicesRef.current.get(slice.index);
        if (pending && pending.length === h * w) before = pending.slice();
        else if (slice.before_runs) before = decodeRuns(slice.before_runs, h * w);
        else {
          const response = await labelRunsFor(slice.index, undefined, plannedAxis);
          if (plannedAxis !== axisRef.current || idsIndexRef.current !== indexRef.current) return false;
          before = decodeRuns(response.runs, h * w);
        }
      }
      const after = decodeRuns(slice.runs, h * w);
      if (roiOnlyRef.current) {
        const regionUrl = await sliceRegionUrl(slice.index, undefined, plannedAxis);
        if (!regionUrl) return false;
        const region = await decodeRegionMask(regionUrl, w, h);
        protectHiddenRegionLabels(before, after, region, regionVisibleIdsFor(before, region));
      }
      if (before.every((value, offset) => value === after[offset])) continue;
      compound.push({ index: slice.index, before, after });
      for (const value of after) if (value > maxLabel) maxLabel = value;
      if (sliceOffset % 2 === 1) await yieldToMainThread();
    }
    if (!historyRef.current.recordCompound(compound)) return false;
    // What was applied, kept so Track's Reject can restore it exactly. The
    // plan is only in the pending buffer (never on disk), so undoing it is a
    // client-side operation, not a server snapshot restore.
    lastAppliedPlanRef.current = compound;
    writeCompoundEdits(compound, liveIndex, maxLabel);
    return true;
  }, [labelRunsFor, sliceRegionUrl, writeCompoundEdits]);

  const runSplitComponentsNow = useCallback(
    async (labelOverride?: number) => {
      const label = labelOverride ?? activeId;
      if (!label || label < 1 || splitRunning) return;
      setSplitRunning(true);
      try {
        const result = await runSplitComponents(
          taskId, label, axisRef.current, pendingToolSlices(),
        );
        await applyPendingToolPlan(result.axis, result.slices);
      } catch (e) {
        window.alert(e instanceof Error ? e.message : "Split failed");
      } finally {
        setSplitRunning(false);
      }
    },
    [
      taskId,
      activeId,
      splitRunning,
      applyPendingToolPlan,
      pendingToolSlices,
    ],
  );

  const runMergeLabelsNow = useCallback(async () => {
    const a = mergeIdA;
    const b = mergeIdB;
    if (
      mergeRunning ||
      a == null ||
      b == null ||
      a < 1 ||
      b < 1 ||
      a === b
    ) {
      return;
    }
    setMergeRunning(true);
    try {
      const result = await runMergeLabels(
        taskId, a, b, axisRef.current, pendingToolSlices(),
      );
      if (!(await applyPendingToolPlan(result.axis, result.slices))) return;
      setActiveId(result.kept_label);
      setMergeIdA(result.kept_label);
      // The removed label must never remain selected. Keep the surviving
      // label as the first input so the next click can merge another into it.
      setMergeIdB(null);
      mergeClickSlotRef.current = 1;
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Merge failed");
    } finally {
      setMergeRunning(false);
    }
  }, [
    taskId,
    mergeIdA,
    mergeIdB,
    mergeRunning,
    applyPendingToolPlan,
    pendingToolSlices,
  ]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      // Swapped = the 2D canvas is the small, view-only preview (#31 item
      // 5) — paint tools stay inert. Plain View / share still allow Select-
      // like label picking so the Labels list can follow the cursor.
      if (swapped) return;
      if (
        // `status` is React state and lags the click by a render, so a stroke
        // can slip in just after Save starts. `saveInFlightRef` is assigned
        // synchronously and closes that window. Such a stroke is never lost
        // (its newer revision keeps the slice pending), but it does make the
        // flush report "not everything saved", which a following Merge/Split
        // reads as a failed flush and aborts on.
        status === "saving" ||
        saveInFlightRef.current !== null ||
        wsRunning ||
        splitRunning ||
        mergeRunning ||
        deleteRunning ||
        // Interpolation plans in a worker and then installs one compound undo
        // entry. Keep painting out until that atomic pending apply completes.
        interpRunning ||
        floodRunning ||
        tracking
      ) return;
      if (e.button !== 0) return;
      // Buffer belongs to another z until loadSlice finishes — do not paint.
      if (idsIndexRef.current !== index) return;
      const pt = pixelFromEvent(e);
      if (!pt) return;
      const [py, px] = pt;
      const ids = idsRef.current;

      // View / share: eyedropper only (same as Select). No paint tools.
      if (!editable) {
        if (ids) {
          const [, w] = shapeRef.current;
          const picked = ids[py * w + px];
          if (picked > 0) selectLabelFromCanvas(picked);
        }
        return;
      }

      // Select tool (or Shift+click, except while placing AI points — there
      // Shift means "negative point") = eyedropper, never paints.
      if (paintTool === "select" || (e.shiftKey && !AI_POINT_TOOLS.includes(paintTool))) {
        if (ids) {
          const [, w] = shapeRef.current;
          const picked = ids[py * w + px];
          if (picked > 0) selectLabelFromCanvas(picked);
        }
        return;
      }

      if (AI_POINT_TOOLS.includes(paintTool)) {
        if (e.altKey) {
          // Alt+click an existing prompt point removes it and re-predicts
          // (or clears the preview if none remain) — Cellable-level prompt
          // editing fluency (#25 item E).
          const nearestIdx = nearestCommittedPointIndex(px, py, 12);
          if (nearestIdx >= 0) {
            const next = aiPointsRef.current.slice();
            next.splice(nearestIdx, 1);
            aiPointsRef.current = next;
            setAiPointCount(next.length);
            renderOverlay();
            runPredictPoints();
          }
          return;
        }
        if (!e.ctrlKey && !e.metaKey) {
          // Clicking on (not near-but-off) an existing committed point
          // drags it instead of adding a new one — Cellable-level vertex
          // drag for AI prompts (#29 item U8). Re-predicts live while
          // dragging (cheap — same coalesced path as the cursor tip) and
          // once more, non-live, on release.
          const dragIdx = nearestCommittedPointIndex(px, py, 10);
          if (dragIdx >= 0) {
            draggingPointIdxRef.current = dragIdx;
            aiTipRef.current = null; // no phantom hover tip while dragging a committed point
            (e.target as Element).setPointerCapture(e.pointerId);
            renderCursorOverlay();
            return;
          }
        }
        // Plain click pins the cursor tip as a new committed point — the
        // tip itself resets to null; the very next pointermove repopulates
        // it at the (possibly unchanged) cursor position, so the free-
        // floating marker "continues from the new last point" (Cellable's
        // `addPoint(line.points[1])`) without drawing a duplicate dot on
        // top of the just-committed one in the meantime.
        aiPointsRef.current = [...aiPointsRef.current, { x: px, y: py, label: e.shiftKey ? 0 : 1 }];
        aiTipRef.current = null;
        setAiPointCount(aiPointsRef.current.length);
        renderOverlay();
        // Ctrl/Cmd+click = add this point and immediately finalize, same as
        // Cellable's "Ctrl+LeftClick ends" (#25 item C.3) — `finalizeAiPoints`
        // itself re-predicts committed-only then commits, so this doesn't
        // double-predict; a plain click instead runs the normal (non-
        // finalizing) committed-only predict for live feedback.
        if (e.ctrlKey || e.metaKey) finalizeAiPoints();
        else runPredictPoints();
        return;
      }

      if (paintTool === "box_mask" || paintTool === "box_eraser") {
        boxDragRef.current = { x0: px, y0: py, x1: px, y1: py };
        drawingRef.current = true;
        (e.target as Element).setPointerCapture(e.pointerId);
        renderOverlay();
        return;
      }

      if (paintTool === "seeds") {
        if (!ids) return;
        const [, w] = shapeRef.current;
        const label = ids[py * w + px];
        if (wsSeeds.length === 0) {
          if (label <= 0) return;
          setWsTargetLabel(label);
          const v = voxelFromSlice(axis, index, py, px);
          setWsSeeds([{ ...v, label }]);
        } else {
          if (label !== wsTargetLabel) return;
          setWsSeeds((prev) => [...prev, { ...voxelFromSlice(axis, index, py, px), label }]);
        }
        renderOverlay();
        return;
      }

      if (paintTool === "flood_fill") {
        void runFloodFill(py, px);
        return;
      }

      if (paintTool === "interpolate") {
        if (!ids || interpRunning) return;
        const [, w] = shapeRef.current;
        const label = ids[py * w + px];
        const layer = idsIndexRef.current;
        if (layer == null) return;
        const next = applyInterpolateCanvasClick(
          label,
          layer,
          interpAnchorRef.current,
          interpFirst,
          interpLast,
        );
        if (!next) return;
        setActiveId(next.activeId);
        interpAnchorRef.current = next.anchor;
        setInterpFirst(next.interpFirst);
        setInterpLast(next.interpLast);
        return;
      }

      if (paintTool === "split_3d") {
        if (!ids || splitRunning) return;
        const [, w] = shapeRef.current;
        const label = ids[py * w + px];
        if (label <= 0) return;
        setActiveId(label);
        void runSplitComponentsNow(label);
        return;
      }

      if (paintTool === "merge") {
        if (!ids || mergeRunning) return;
        const [, w] = shapeRef.current;
        const label = ids[py * w + px];
        const slot = mergeClickSlotRef.current;
        const next = applyMergeCanvasClick(label, mergeIdA, mergeIdB, slot);
        if (!next) return;
        setMergeIdA(next.mergeIdA);
        setMergeIdB(next.mergeIdB);
        mergeClickSlotRef.current = next.nextSlot;
        return;
      }

      // Delete is driven by the explicit, confirmed button in its context
      // row. Clicking the canvas must not accidentally start a brush stroke.
      if (paintTool === "delete") return;

      // Brush / Erase (circular) — existing painting flow.
      // Defer the undo entry until pointer-up proves pixels actually changed;
      // a no-op click used to leave a fake Undo step that looked broken.
      if (ids) {
        historyRef.current.beginStroke(ids);
        syncHistoryCounts();
      }
      drawingRef.current = true;
      lastPointRef.current = null;
      (e.target as Element).setPointerCapture(e.pointerId);
      strokeTo(py, px);
    },
    [
      editable,
      swapped,
      pixelFromEvent,
      paintTool,
      wsSeeds,
      wsTargetLabel,
      index,
      strokeTo,
      syncHistoryCounts,
      renderOverlay,
      renderCursorOverlay,
      runPredictPoints,
      finalizeAiPoints,
      nearestCommittedPointIndex,
      runSplitComponentsNow,
      splitRunning,
      mergeRunning,
      deleteRunning,
      mergeIdA,
      mergeIdB,
      status,
      wsRunning,
      tracking,
      floodRunning,
      runFloodFill,
      interpRunning,
      interpFirst,
      interpLast,
      selectLabelFromCanvas,
    ],
  );

  // Double-click turns either a Point/Boundary or Box proposal directly into
  // the active label. The Box path also remembers the gesture when prediction
  // is still in flight and commits as soon as its preview arrives.
  const onDoubleClick = useCallback(() => {
    if (AI_POINT_TOOLS.includes(paintTool)) {
      finalizeAiPoints();
      return;
    }
    if (paintTool === "box_mask") {
      if (hasAiPreview) {
        finalizeBoxWhenReadyRef.current = false;
        commitAiPreview();
      } else {
        finalizeBoxWhenReadyRef.current = true;
      }
    }
  }, [paintTool, hasAiPreview, finalizeAiPoints, commitAiPreview]);

  // Minimal right-click context menu (#29 item U15) — mode switches always,
  // plus Verify/Solo when right-clicking on an actual label. Deliberately
  // small (Cellable's own canvas context menu is a handful of items, not a
  // full command palette).
  const onContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      if (!editable || swapped) return;
      const pt = pixelFromEvent(e);
      const ids = idsRef.current;
      let labelId: number | null = null;
      if (pt && ids) {
        const [, w] = shapeRef.current;
        const v = ids[pt[0] * w + pt[1]];
        if (v > 0) labelId = v;
      }
      setContextMenu({
        x: Math.max(8, Math.min(e.clientX, window.innerWidth - 176)),
        y: Math.max(8, Math.min(e.clientY, window.innerHeight - 480)),
        labelId,
      });
    },
    [editable, swapped, pixelFromEvent],
  );

  // Close the context menu on any click outside it (or Escape, handled in
  // the keydown effect above).
  useEffect(() => {
    if (!contextMenu) return;
    const onPointerDownOutside = (e: PointerEvent) => {
      if (contextMenuRef.current && !contextMenuRef.current.contains(e.target as Node)) {
        setContextMenu(null);
      }
    };
    window.addEventListener("pointerdown", onPointerDownOutside);
    return () => window.removeEventListener("pointerdown", onPointerDownOutside);
  }, [contextMenu]);

  // Repopulates `intensityCtxRef` from the currently displayed `<img>` —
  // called from the `<img>`'s own `onLoad`, so it always has this slice's
  // actual pixels (not last slice's, not a blank canvas) by the time a
  // pointer move needs to read from it. Deliberately reads the *undisplayed*
  // image (before CSS brightness/contrast), matching "intensity from the
  // displayed slice" in the brief without also needing to bake the CSS
  // filter into a canvas read (browsers don't expose the post-filter pixels
  // via `getImageData` anyway — filters are compositor-side).
  const updateIntensityCanvas = useCallback(() => {
    const img = imgRef.current;
    if (!img || !img.naturalWidth || !img.naturalHeight) return;
    const canvas = intensityCtxRef.current?.canvas ?? document.createElement("canvas");
    if (canvas.width !== img.naturalWidth || canvas.height !== img.naturalHeight) {
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
    }
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return;
    ctx.drawImage(img, 0, 0);
    intensityCtxRef.current = ctx;
  }, []);

  // Status readout under the canvas (#29 item U3, Cellable's status bar
  // `"Mouse is at: slice=…, x=…, y=…, intensity=…, label=…"`) — a direct DOM
  // write (`statusReadoutRef`), not React state, since pointer moves fire
  // far too often to route through a re-render (same reasoning as the
  // overlay canvas itself). Intensity reads the *undisplayed* slice image
  // (`intensityCtxRef`, populated on every slice load's `<img>` onLoad) —
  // "from the displayed slice" per the brief, deliberately not the raw
  // backend array, so no extra fetch is needed just for this readout.
  const updateStatusReadout = useCallback(
    (pt: [number, number] | null) => {
      const el = statusReadoutRef.current;
      if (!el) return;
      if (!pt) {
        el.textContent = "";
        return;
      }
      const [py, px] = pt;
      const ids = idsRef.current;
      const [, w] = shapeRef.current;
      const label = ids ? ids[py * w + px] : 0;
      let intensity: number | string = "–";
      const ictx = intensityCtxRef.current;
      if (ictx) {
        try {
          intensity = ictx.getImageData(px, py, 1, 1).data[0];
        } catch {
          intensity = "–";
        }
      }
      el.textContent = `${axisShortLabel(axis)} ${index + 1} · x ${px}, y ${py} · intensity ${intensity} · label ${label > 0 ? label : "–"}`;
    },
    [index, axis],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const pt = pixelFromEvent(e);
      hoverPosRef.current = pt;
      updateStatusReadout(pt);
      if (pt && AI_PREVIEW_TOOLS.includes(paintTool)) {
        const roiKey = `${axis}:${index}:${Math.floor(pt[1] / 256)}:${Math.floor(pt[0] / 256)}`;
        if (roiKey !== lastRoiWarmRef.current) {
          if (roiWarmTimerRef.current) clearTimeout(roiWarmTimerRef.current);
          roiWarmTimerRef.current = setTimeout(() => {
            lastRoiWarmRef.current = roiKey;
            warmEmbedding(taskId, axis, index, undefined, [pt[1], pt[0]]).catch(() => {});
          }, 120);
        }
      }
      if (draggingPointIdxRef.current != null) {
        // Dragging a committed AI prompt point (#29 item U8) — update its
        // position in place and live-predict (cheap, coalesced — same path
        // as the cursor tip); a final non-live predict fires on release.
        if (pt) {
          const idx = draggingPointIdxRef.current;
          const pts = aiPointsRef.current;
          if (idx < pts.length) {
            const next = pts.slice();
            next[idx] = { ...next[idx], x: pt[1], y: pt[0] };
            aiPointsRef.current = next;
          }
        }
        renderCursorOverlay();
        scheduleLivePredict();
        return;
      }
      if (paintTool === "box_mask" || paintTool === "box_eraser") {
        if (drawingRef.current && boxDragRef.current && pt) {
          boxDragRef.current = { ...boxDragRef.current, x1: pt[1], y1: pt[0] };
        }
        // Rubber-band drag + crosshair are both cheap re-blit + vector
        // redraws — never touch the O(h*w) label recompute mid-drag.
        renderCursorOverlay();
        return;
      }
      if (paintTool === "brush" || paintTool === "eraser") {
        if (drawingRef.current && pt) strokeTo(pt[0], pt[1]);
        else renderCursorOverlay(); // just move the size-cursor circle
        return;
      }
      if (AI_POINT_TOOLS.includes(paintTool)) {
        // Cursor-follow live proposal (#27) — no tip (and no preview at
        // all) until ≥1 point is committed, matching Cellable's `if not
        // self.current: return`. `renderCursorOverlay` moves the tip vertex
        // immediately every move (smooth tracking); `scheduleLivePredict`
        // is throttled and only actually updates the green fill once its
        // (possibly delayed) network response lands.
        aiTipRef.current = pt && aiPointsRef.current.length > 0 ? { x: pt[1], y: pt[0], label: e.shiftKey ? 0 : 1 } : null;
        renderCursorOverlay();
        scheduleLivePredict();
        return;
      }
      if (paintTool === "seeds" || paintTool === "flood_fill") {
        renderCursorOverlay();
        return;
      }
    },
    [
      pixelFromEvent,
      updateStatusReadout,
      paintTool,
      axis,
      index,
      taskId,
      strokeTo,
      renderCursorOverlay,
      scheduleLivePredict,
    ],
  );

  useEffect(
    () => () => {
      if (roiWarmTimerRef.current) clearTimeout(roiWarmTimerRef.current);
    },
    [],
  );

  const onPointerUp = useCallback(() => {
    if (draggingPointIdxRef.current != null) {
      // Drag ends — re-predict once more, non-live, so the settled position
      // gets a real (not last-coalesced-frame) proposal (#29 item U8).
      draggingPointIdxRef.current = null;
      runPredictPoints();
      return;
    }
    if (paintTool === "box_mask" || paintTool === "box_eraser") {
      const box = boxDragRef.current;
      drawingRef.current = false;
      if (!box) return;
      const x0 = Math.min(box.x0, box.x1);
      const x1 = Math.max(box.x0, box.x1);
      const y0 = Math.min(box.y0, box.y1);
      const y1 = Math.max(box.y0, box.y1);
      const hasArea = x1 > x0 && y1 > y0;
      if (paintTool === "box_eraser") {
        boxDragRef.current = null;
        if (hasArea) applyBoxErase(y0, y1, x0, x1);
        else renderOverlay();
      } else {
        boxDragRef.current = null;
        if (hasArea) runPredictBox([[x0, y0], [x1, y1]]);
        else renderOverlay();
      }
      return;
    }
    if (!drawingRef.current) return;
    finishOpenStroke();
  }, [paintTool, applyBoxErase, runPredictBox, renderOverlay, markDirty, runPredictPoints, finishOpenStroke]);

  const onPointerCancel = useCallback(() => {
    finishOpenStroke();
    boxDragRef.current = null;
    draggingPointIdxRef.current = null;
    drawingRef.current = false;
  }, [finishOpenStroke]);

  const onPointerLeave = useCallback(() => {
    hoverPosRef.current = null;
    updateStatusReadout(null);
    // A drag in progress when the pointer leaves still needs the same
    // release handling `onPointerUp` gives it (clear the drag ref,
    // re-predict once more) — `onPointerUp()` below already covers this
    // since it's the same function, just also reachable via leave.
    onPointerUp();
    // Tip leaves the canvas (#27 item 5) — drop it and immediately (not
    // throttled — this is a discrete leave, not a move stream) re-predict
    // committed-only so the proposal snaps back rather than sitting on a
    // stale off-canvas tip until the next move.
    if (AI_POINT_TOOLS.includes(paintTool) && aiTipRef.current) {
      aiTipRef.current = null;
      runPredictPoints();
    }
    renderCursorOverlay();
  }, [onPointerUp, paintTool, runPredictPoints, renderCursorOverlay, updateStatusReadout]);

  const applyHistoryResult = useCallback(
    (
      result: { kind: "slice"; raster: Int32Array } | {
        kind: "compound";
        slices: { index: number; before: Int32Array; after: Int32Array }[];
      },
      direction: "undo" | "redo",
    ) => {
      const liveZ = idsIndexRef.current;
      if (result.kind === "slice") {
        idsRef.current = result.raster;
        if (liveZ != null) {
          pendingSlicesRef.current.markChanged(liveZ, result.raster);
        }
      } else {
        for (const edit of result.slices) {
          const plane = direction === "undo" ? edit.before : edit.after;
          if (liveZ != null && edit.index === liveZ && idsRef.current) {
            idsRef.current.set(plane);
            pendingSlicesRef.current.markChanged(edit.index, idsRef.current);
          } else {
            pendingSlicesRef.current.markChanged(edit.index, plane.slice());
          }
        }
      }
      renderOverlay();
      refreshInstances();
      syncHistoryCounts();
      markDirty();
    },
    [renderOverlay, refreshInstances, syncHistoryCounts, markDirty],
  );

  const undo = useCallback(() => {
    // Refuse while the displayed index and the loaded buffer disagree — that
    // window is exactly when a shared-stack pop used to empty another slice's
    // parked history and make Undo look randomly broken.
    if (idsIndexRef.current !== indexRef.current) return;
    if (status === "saving" || saveInFlightRef.current) return;
    const ids = idsRef.current;
    if (!ids) return;
    if (historyRef.current.hasOpenStroke) {
      historyRef.current.cancelStroke();
    }
    const prev = historyRef.current.undo(ids);
    if (!prev) {
      syncHistoryCounts();
      return;
    }
    applyHistoryResult(prev, "undo");
  }, [status, applyHistoryResult, syncHistoryCounts]);

  const redo = useCallback(() => {
    if (idsIndexRef.current !== indexRef.current) return;
    if (status === "saving" || saveInFlightRef.current) return;
    const ids = idsRef.current;
    if (!ids) return;
    if (historyRef.current.hasOpenStroke) {
      historyRef.current.cancelStroke();
    }
    const next = historyRef.current.redo(ids);
    if (!next) {
      syncHistoryCounts();
      return;
    }
    applyHistoryResult(next, "redo");
  }, [status, applyHistoryResult, syncHistoryCounts]);

  const deleteSlice = useCallback(() => {
    const ids = idsRef.current;
    if (!ids || !ids.some((v) => v > 0)) return;
    if (!window.confirm("Clear all instances from this layer? This only affects the layer on screen — other layers are untouched.")) {
      return;
    }
    const before = roiOnlyRef.current ? ids.slice() : null;
    const changed = historyRef.current.withChange(ids, () => {
      const region = roiOnlyRef.current ? regionMaskBitsRef.current?.mask : null;
      const visible = region?.length === ids.length
        ? labelIdsTouchingRegion(ids, region)
        : null;
      if (roiOnlyRef.current && !visible) return;
      for (let i = 0; i < ids.length; i++) {
        if (visible && ids[i] > 0 && !visible.has(ids[i])) continue;
        ids[i] = 0;
      }
    });
    if (!changed) return;
    if (before && idsIndexRef.current != null) {
      recordOutsideChanges(idsIndexRef.current, before, ids);
    }
    renderOverlay();
    refreshInstances();
    syncHistoryCounts();
    markDirty();
  }, [recordOutsideChanges, renderOverlay, refreshInstances, syncHistoryCounts, markDirty]);

  /** Discard this task's whole working annotation and re-seed it from the
   * registered label mask.
   *
   * Unlike every other tool here this is *not* a pending edit: the server
   * rewrites the working file immediately, so afterwards the in-memory state
   * must be dropped wholesale — pending planes, undo history, outside-region
   * records, the Track queue, the ROI membership set. Keeping any of it would
   * show, and eventually Save, work that no longer exists on disk. */
  const resetLabelsToRegistered = useCallback(async () => {
    if (resetRunning) return;
    if (!window.confirm(
      "Reset this task's labels to the registered mask?\n\n"
      + "Every annotation on this volume's working copy is discarded — all layers, "
      + "saved and unsaved, plus Track prompts and label verification state. "
      + "The registered source mask is not changed. This cannot be undone.",
    )) {
      return;
    }
    setResetRunning(true);
    try {
      await resetWorkingLabels(taskId);
      // Order matters: drop local state *before* reloading, so nothing can
      // re-stash a pending plane over the freshly restored mask.
      pendingSlicesRef.current.clear();
      outsideEditsRef.current.clear();
      clearAllHistory();
      historyRef.current.clearAll();
      dirtyRef.current = false;
      currentSliceDirtyRef.current = false;
      baselineIdsRef.current = null;
      regionTouchingIdsRef.current = null;
      regionMembershipBaseRef.current = null;
      trackPromptDraftRef.current = null;
      trackPromptProposalRef.current = null;
      trackUndoRef.current = [];
      trackRedoRef.current = [];
      // Set the counts directly: `syncTrackingHistoryCounts` is declared with
      // the rest of the Track code further down, and both stacks are empty.
      setTrackUndoCount(0);
      setTrackRedoCount(0);
      setTrackingPrompts([]);
      setTrackingPendingReview(null);
      setSelectedTrackParent(null);
      setSelectedTrackSubclass(null);
      trackPreviewUndoRef.current = null;
      setDirty(false);
      setStatus("idle");
      setOutsideEditRevision((v) => v + 1);
      syncHistoryCounts();
      await loadSlice(index, undefined, { forceServer: true });
      setLabelsSummaryToken((v) => v + 1);
      setRegionMembershipToken((v) => v + 1);
      setLabels3DRefreshKey((v) => v + 1);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Could not reset labels");
    } finally {
      setResetRunning(false);
    }
  }, [
    clearAllHistory,
    index,
    loadSlice,
    resetRunning,
    syncHistoryCounts,
    taskId,
  ]);

  const clearWsSeeds = useCallback(() => {
    setWsSeeds([]);
    setWsTargetLabel(null);
    renderOverlay();
  }, [renderOverlay]);

  const runWatershedNow = useCallback(async () => {
    if (!wsTargetLabel || wsSeeds.length === 0) return;
    setWsRunning(true);
    try {
      const seeds: WatershedSeed[] = wsSeeds.map(({ z, y, x }) => ({ z, y, x }));
      const result = await runWatershed(
        taskId,
        wsTargetLabel,
        seeds,
        axisRef.current,
        pendingToolSlices(),
      );
      if (!(await applyPendingToolPlan(result.axis, result.slices))) return;
      setWsSeeds([]);
      setWsTargetLabel(null);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Watershed failed");
    } finally {
      setWsRunning(false);
    }
  }, [taskId, wsTargetLabel, wsSeeds, applyPendingToolPlan, pendingToolSlices]);

  // --- Track (SAM2): durable parent-class queue + local child classes -----

  const syncTrackingHistoryCounts = useCallback(() => {
    setTrackUndoCount(trackUndoRef.current.length);
    setTrackRedoCount(trackRedoRef.current.length);
  }, []);

  const recordTrackingHistory = useCallback(() => {
    trackUndoRef.current.push(snapshotTrackingPromptGeometry(trackingPrompts));
    trackRedoRef.current = [];
    syncTrackingHistoryCounts();
  }, [syncTrackingHistoryCounts, trackingPrompts]);

  const persistTrackingPrompt = useCallback(async (prompt: TrackingPrompt) => {
    const saved = await putTrackingPrompt(taskId, prompt);
    setTrackingPrompts((items) => {
      const rest = items.filter((item) => item.parent_id !== saved.parent_id);
      return [...rest, saved];
    });
    return saved;
  }, [taskId]);

  const selectTrackingPrompt = useCallback((parentId: number) => {
    const prompt = trackingPrompts.find((item) => item.parent_id === parentId);
    if (!prompt) return;
    setSelectedTrackParent(parentId);
    setSelectedTrackSubclass(prompt.subclasses[0]?.index ?? null);
    setActiveId(parentId);
    const seedZs = prompt.subclasses.flatMap((subclass) => subclass.seeds.map((seed) => seed.z));
    if (seedZs.length) requestIndex(Math.min(...seedZs));
  }, [trackingPrompts, requestIndex]);

  const queueActiveTrackingPrompt = useCallback(async () => {
    setTrackError(null);
    const existing = trackingPrompts.find((item) => item.parent_id === activeId);
    if (existing) {
      selectTrackingPrompt(activeId);
      return;
    }
    const prompt: TrackingPrompt = {
      parent_id: activeId,
      subclasses: [{ index: 1, seeds: [] }],
      z_range: [index, index],
      status: "draft",
    };
    try {
      await persistTrackingPrompt(prompt);
      setSelectedTrackParent(activeId);
      setSelectedTrackSubclass(1);
    } catch (e) {
      setTrackError(e instanceof Error ? e.message : "Could not queue parent class");
    }
  }, [activeId, index, persistTrackingPrompt, selectTrackingPrompt, trackingPrompts]);

  const addTrackingSubclass = useCallback(async () => {
    const prompt = trackingPrompts.find((item) => item.parent_id === selectedTrackParent);
    if (!prompt) return;
    const next = Math.max(0, ...prompt.subclasses.map((subclass) => subclass.index)) + 1;
    try {
      await persistTrackingPrompt({ ...prompt, subclasses: [...prompt.subclasses, { index: next, seeds: [] }], status: "draft" });
      setSelectedTrackSubclass(next);
    } catch (e) {
      setTrackError(e instanceof Error ? e.message : "Could not add child class");
    }
  }, [persistTrackingPrompt, selectedTrackParent, trackingPrompts]);

  const removeTrackingSubclass = useCallback(async (subclassIndex: number) => {
    const prompt = trackingPrompts.find((item) => item.parent_id === selectedTrackParent);
    if (!prompt) return;
    const subclasses = prompt.subclasses.filter((subclass) => subclass.index !== subclassIndex);
    try {
      await persistTrackingPrompt({ ...prompt, subclasses, z_range: trackingRange(subclasses), status: subclasses.some((subclass) => subclass.seeds.length) ? "ready" : "draft" });
      setSelectedTrackSubclass(subclasses[0]?.index ?? null);
    } catch (e) {
      setTrackError(e instanceof Error ? e.message : "Could not remove child class");
    }
  }, [persistTrackingPrompt, selectedTrackParent, trackingPrompts]);

  const removeTrackingPrompt = useCallback(async () => {
    if (selectedTrackParent == null) return;
    try {
      await deleteTrackingPrompt(taskId, selectedTrackParent);
      const remaining = trackingPrompts.filter((item) => item.parent_id !== selectedTrackParent);
      setTrackingPrompts(remaining);
      setSelectedTrackParent(remaining[0]?.parent_id ?? null);
      setSelectedTrackSubclass(remaining[0]?.subclasses[0]?.index ?? null);
    } catch (e) {
      setTrackError(e instanceof Error ? e.message : "Could not remove parent class");
    }
  }, [selectedTrackParent, taskId, trackingPrompts]);

  const propagateTrackingQueue = useCallback(async (selectedOnly: boolean) => {
    const parentIds = trackingPrompts
      .filter((prompt) => prompt.subclasses.some((subclass) => subclass.seeds.length))
      .filter((prompt) => selectedOnly
        ? prompt.parent_id === selectedTrackParent
        : true)
      .map((prompt) => prompt.parent_id);
    if (!parentIds.length) {
      setTrackError("Draw at least one child-class seed before propagating.");
      return;
    }
    setTracking(true);
    setTrackingParentIds(parentIds);
    setTrackError(null);
    // A live Box/Point proposal and an in-flight seed write are both part of
    // "what the annotator drew". Commit and flush them *before* the request
    // goes out, so the server propagates the seeds actually on screen rather
    // than whichever ones happened to be durable already.
    await commitTrackingProposalRef.current().catch(() => false);
    await trackPromptSaveChainRef.current.catch(() => false);
    setTrackPromptTool(null);
    trackPromptProposalRef.current = null;
    trackPromptPointsRef.current = { key: "", points: [] };
    setTrackingPrompts((items) => items.map((item) => parentIds.includes(item.parent_id) ? { ...item, status: "running" } : item));
    try {
      const result = await trackTaskBatch(
        taskId,
        parentIds,
        axisRef.current,
        pendingToolSlices(),
        trackOverwriteMode,
      );
      lastAppliedPlanRef.current = null;
      if (!(await applyPendingToolPlan(result.axis, result.slices))) {
        throw new Error("Track result became stale before it could be applied.");
      }
      // The batch endpoint *plans* — it returns planes for the pending buffer
      // and never writes labels or a server-side pending review. The review
      // step is therefore ours to hold: mark the propagated parents pending so
      // Confirm/Reject light up, and remember the compound Reject must undo.
      trackPreviewUndoRef.current = {
        parentIds,
        edits: lastAppliedPlanRef.current ?? [],
      };
      setTrackingPrompts((items) => items.map((item) =>
        parentIds.includes(item.parent_id) ? { ...item, status: "pending" } : item
      ));
      setTrackingPendingReview({ parent_ids: parentIds, status: "pending_review", local: true });
    } catch (e) {
      const message = e instanceof Error ? e.message : "Tracking failed";
      if (message.includes("Confirm or Reject")) {
        try {
          await syncTrackingQueue();
          setTrackError(null);
        } catch (syncError) {
          setTrackError(syncError instanceof Error ? syncError.message : message);
        }
      } else {
        setTrackingPrompts((items) => items.map((item) => parentIds.includes(item.parent_id) ? { ...item, status: "error" } : item));
        setTrackError(message);
      }
    } finally {
      setTracking(false);
      setTrackingParentIds([]);
    }
  }, [applyPendingToolPlan, pendingToolSlices, selectedTrackParent, syncTrackingQueue, taskId, trackOverwriteMode, trackingPrompts]);

  const trackingPromptKey = useCallback(() => {
    const [h, w] = shapeRef.current;
    return selectedTrackParent != null && selectedTrackSubclass != null
      ? `${selectedTrackParent}:${selectedTrackSubclass}:${index}:${h}x${w}`
      : "";
  }, [index, selectedTrackParent, selectedTrackSubclass]);

  const currentTrackingPromptMask = useCallback(() => {
    const [h, w] = shapeRef.current;
    const key = trackingPromptKey();
    if (!key || h === 0 || w === 0) return null;
    if (trackPromptDraftRef.current?.key === key) return trackPromptDraftRef.current.mask;
    const prompt = trackingPrompts.find((item) => item.parent_id === selectedTrackParent);
    const child = prompt?.subclasses.find((item) => item.index === selectedTrackSubclass);
    const seed = child?.seeds.find((item) => item.z === index);
    const mask = seed && seed.shape[0] === h && seed.shape[1] === w
      ? maskFromTrackingSeed(seed.rle, h * w)
      : new Uint8Array(h * w);
    trackPromptDraftRef.current = { key, mask };
    if (trackPromptPointsRef.current.key !== key) trackPromptPointsRef.current = { key, points: [] };
    return mask;
  }, [index, selectedTrackParent, selectedTrackSubclass, trackingPromptKey, trackingPrompts]);

  const saveTrackingPromptMask = useCallback((mask: Uint8Array) => {
    // Serialize formal mask writes. Brush strokes can finish close together;
    // invocation order must also be server order so an older mask cannot land
    // after the newer cumulative mask while Save progress is waiting.
    const operation = trackPromptSaveChainRef.current.then(async (): Promise<boolean> => {
      const prompt = trackingPrompts.find((item) => item.parent_id === selectedTrackParent);
      if (!prompt || selectedTrackSubclass == null) return false;
      const [h, w] = shapeRef.current;
      const any = mask.some(Boolean);
      const subclasses = prompt.subclasses.map((child) => {
        if (child.index !== selectedTrackSubclass) return child;
        const otherSeeds = child.seeds.filter((seed) => seed.z !== index);
        return {
          ...child,
          seeds: any
            ? [...otherSeeds, { z: index, rle: trueRunsRLE(mask), shape: [h, w] as [number, number] }]
            : otherSeeds,
        };
      });
      const next: TrackingPrompt = {
        ...prompt,
        subclasses,
        z_range: trackingRange(subclasses),
        status: subclasses.some((child) => child.seeds.length) ? "ready" : "draft",
      };
      // Optimistic replacement makes the queue and seed markers respond
      // immediately; the durable queue remains the source used by propagation.
      recordTrackingHistory();
      setTrackingPrompts((items) => items.map((item) => item.parent_id === next.parent_id ? next : item));
      try {
        await persistTrackingPrompt(next);
        setTrackError(null);
        return true;
      } catch (e) {
        setTrackError(e instanceof Error ? e.message : "Could not save child-class prompt");
        const queue = await getTrackingPrompts(taskId).catch(() => null);
        if (queue) setTrackingPrompts(queue.items);
        return false;
      }
    });
    trackPromptSaveChainRef.current = operation;
    trackPromptSavePromiseRef.current = operation;
    void operation.finally(() => {
      if (trackPromptSavePromiseRef.current === operation) trackPromptSavePromiseRef.current = null;
    });
    return operation;
  }, [index, persistTrackingPrompt, recordTrackingHistory, selectedTrackParent, selectedTrackSubclass, taskId, trackingPrompts]);

  const discardTrackingProposal = useCallback(() => {
    trackPromptPredictSeqRef.current += 1;
    trackPromptPredictingRef.current = false;
    trackPromptPredictionPromiseRef.current = null;
    trackPromptFinalizeWhenReadyRef.current = false;
    trackPromptProposalRef.current = null;
    trackPromptBoxRef.current = null;
    trackPromptDrawingRef.current = false;
    trackPromptLastRef.current = null;
    trackPromptPointsRef.current = { key: trackingPromptKey(), points: [] };
    setTrackPromptRevision((value) => value + 1);
  }, [trackingPromptKey]);

  const commitTrackingProposal = useCallback((): Promise<boolean> => {
    if (trackPromptCommitPromiseRef.current) return trackPromptCommitPromiseRef.current;
    const operation = (async () => {
      const proposal = trackPromptProposalRef.current;
      const key = trackingPromptKey();
      if (!proposal || proposal.key !== key) {
        // Double-click may land before the network prediction. Mirror the
        // regular Box finalize path by committing as soon as that in-flight
        // proposal arrives, but never arm a future unrelated prediction.
        trackPromptFinalizeWhenReadyRef.current = trackPromptPredictingRef.current;
        return false;
      }
      trackPromptPredictSeqRef.current += 1;
      trackPromptPredictingRef.current = false;
      trackPromptFinalizeWhenReadyRef.current = false;
      trackPromptDraftRef.current = { key, mask: proposal.mask.slice() };
      trackPromptPointsRef.current = { key, points: [] };
      setTrackPromptRevision((value) => value + 1);
      const saved = await saveTrackingPromptMask(proposal.mask.slice());
      if (saved && trackPromptProposalRef.current?.key === key) {
        trackPromptProposalRef.current = null;
        setTrackPromptRevision((value) => value + 1);
      }
      return saved;
    })();
    trackPromptCommitPromiseRef.current = operation;
    void operation.finally(() => {
      if (trackPromptCommitPromiseRef.current === operation) trackPromptCommitPromiseRef.current = null;
    });
    return operation;
  }, [saveTrackingPromptMask, trackingPromptKey]);
  commitTrackingProposalRef.current = commitTrackingProposal;

  const stageTrackingProposal = useCallback((key: string, seq: number, mask: Uint8Array) => {
    if (seq !== trackPromptPredictSeqRef.current || key !== trackingPromptKey()) return;
    trackPromptPredictingRef.current = false;
    if (!mask.some(Boolean)) {
      trackPromptFinalizeWhenReadyRef.current = false;
      trackPromptProposalRef.current = null;
      setTrackError("No child-class proposal found — adjust the Box or Point prompts.");
      setTrackPromptRevision((value) => value + 1);
      return;
    }
    setTrackError(null);
    trackPromptProposalRef.current = { key, mask };
    setTrackPromptRevision((value) => value + 1);
    if (trackPromptFinalizeWhenReadyRef.current) void commitTrackingProposal();
  }, [commitTrackingProposal, trackingPromptKey]);

  const changeTrackPromptTool = useCallback((tool: TrackingPromptTool | null) => {
    if (tool !== trackPromptTool) {
      // Picking Brush/Erase/Box erase after a Box or Point prompt means
      // "refine this proposal", so commit it into the durable child seed
      // first. Discarding here is what made brush-after-box silently start
      // from the *previous* seed and lose the AI result. `commit` also arms
      // the finalize-when-ready flag, so a proposal still in flight lands in
      // the seed too rather than being cancelled.
      if (tool != null && MANUAL_PROMPT_TOOLS.includes(tool)) {
        trackPromptBoxRef.current = null;
        trackPromptDrawingRef.current = false;
        void commitTrackingProposalRef.current();
      } else {
        discardTrackingProposal();
      }
    }
    if (tool != null) setTrackProgressSaved(false);
    setTrackPromptTool(tool);
  }, [discardTrackingProposal, trackPromptTool]);

  const saveTrackProgress = useCallback(async () => {
    if (trackPromptTool == null || trackProgressSaving) return;
    setTrackProgressSaving(true);
    setTrackProgressSaved(false);
    setTrackError(null);
    try {
      const prediction = trackPromptPredictionPromiseRef.current;
      if (prediction) await prediction;
      const key = trackingPromptKey();
      const proposal = trackPromptProposalRef.current?.key === key
        ? trackPromptProposalRef.current
        : null;
      if (proposal) {
        if (!await commitTrackingProposal()) return;
      } else if (prediction && !trackPromptSavePromiseRef.current) {
        setTrackError("Could not save progress because the Box/Point proposal is empty. Adjust the prompt and try again.");
        return;
      }
      const pendingSave = trackPromptSavePromiseRef.current;
      if (pendingSave && !await pendingSave) return;
      discardTrackingProposal();
      setTrackPromptTool(null);
      setTrackProgressSaved(true);
    } finally {
      setTrackProgressSaving(false);
    }
  }, [commitTrackingProposal, discardTrackingProposal, trackProgressSaving, trackPromptTool, trackingPromptKey]);

  const restoreTrackingHistory = useCallback(async (direction: "undo" | "redo") => {
    if (trackPromptHistoryBusy || trackingPendingReview) return;
    const source = direction === "undo" ? trackUndoRef.current : trackRedoRef.current;
    const target = source.pop();
    if (!target) {
      syncTrackingHistoryCounts();
      return;
    }
    const destination = direction === "undo" ? trackRedoRef.current : trackUndoRef.current;
    const current = snapshotTrackingPromptGeometry(trackingPrompts);
    destination.push(current);
    syncTrackingHistoryCounts();
    setTrackPromptHistoryBusy(true);
    discardTrackingProposal();
    try {
      const restoredItems = restoreTrackingPromptGeometry(trackingPrompts, target);
      const restored = await replaceTrackingPrompts(taskId, restoredItems);
      setTrackingPrompts(restored.items);
      trackPromptDraftRef.current = null;
      const selected = restored.items.find((item) => item.parent_id === selectedTrackParent) ?? restored.items[0] ?? null;
      setSelectedTrackParent(selected?.parent_id ?? null);
      setSelectedTrackSubclass((childIndex) =>
        selected?.subclasses.some((child) => child.index === childIndex)
          ? childIndex
          : selected?.subclasses[0]?.index ?? null,
      );
      setTrackPromptRevision((value) => value + 1);
      setTrackError(null);
    } catch (error) {
      destination.pop();
      source.push(target);
      syncTrackingHistoryCounts();
      setTrackError(error instanceof Error ? error.message : `Could not ${direction} Track prompt edit`);
    } finally {
      setTrackPromptHistoryBusy(false);
    }
  }, [discardTrackingProposal, selectedTrackParent, syncTrackingHistoryCounts, taskId, trackPromptHistoryBusy, trackingPendingReview, trackingPrompts]);

  const undoTrackingPrompt = useCallback(() => {
    void restoreTrackingHistory("undo");
  }, [restoreTrackingHistory]);

  const redoTrackingPrompt = useCallback(() => {
    void restoreTrackingHistory("redo");
  }, [restoreTrackingHistory]);

  /** Resolve a preview that only ever existed in the pending buffer.
   *
   * Confirm keeps the planned planes exactly where they are (Save is still the
   * only thing that writes them to disk) and retires the propagated parents
   * from the queue. Reject writes each plane's pre-propagation state back
   * through the same compound path, as a new undo step rather than surgery on
   * the history stack. Neither reloads from the server: the propagation is not
   * on the server, so a forced reload would throw the result away either way. */
  const reviewLocalTrackPreview = useCallback(async (action: "confirm" | "reject") => {
    const preview = trackPreviewUndoRef.current;
    const parentIds = preview?.parentIds ?? trackingPendingReview?.parent_ids ?? [];
    if (action === "reject" && preview?.edits.length) {
      const inverse = preview.edits.map((edit) => ({
        index: edit.index,
        before: edit.after,
        after: edit.before,
      }));
      let maxLabel = 0;
      for (const edit of inverse) for (const value of edit.after) if (value > maxLabel) maxLabel = value;
      if (historyRef.current.recordCompound(inverse)) {
        writeCompoundEdits(inverse, idsIndexRef.current, maxLabel);
      }
    }
    const keep = action === "confirm"
      ? trackingPrompts.filter((item) => !parentIds.includes(item.parent_id))
      : trackingPrompts.map((item) => parentIds.includes(item.parent_id)
        ? {
          ...item,
          status: item.subclasses.some((child) => child.seeds.length)
            ? ("ready" as const)
            : ("draft" as const),
        }
        : item);
    // The queue lives on the server even though the pixels do not, so the
    // retire/restore has to land there too — otherwise a reload resurrects
    // parents that were already reviewed.
    const stored = await replaceTrackingPrompts(taskId, keep);
    trackPreviewUndoRef.current = null;
    setTrackingPrompts(stored.items);
    setTrackingPendingReview(null);
    trackPromptDraftRef.current = null;
    if (action === "confirm") {
      trackUndoRef.current = [];
      trackRedoRef.current = [];
      syncTrackingHistoryCounts();
    }
    const selected = stored.items.find((item) => item.parent_id === selectedTrackParent) ?? stored.items[0] ?? null;
    setSelectedTrackParent(selected?.parent_id ?? null);
    setSelectedTrackSubclass(selected?.subclasses[0]?.index ?? null);
    setTrackPromptRevision((value) => value + 1);
    setLabelsSummaryToken((value) => value + 1);
  }, [selectedTrackParent, syncTrackingHistoryCounts, taskId, trackingPendingReview, trackingPrompts, writeCompoundEdits]);

  const reviewTrackPreview = useCallback(async (action: "confirm" | "reject") => {
    if (!trackingPendingReview || trackReviewAction) return;
    setTrackReviewAction(action);
    setTrackError(null);
    changeTrackPromptTool(null);
    try {
      if (trackingPendingReview.local) {
        await reviewLocalTrackPreview(action);
        return;
      }
      const reviewed = await reviewTrackingPreview(taskId, action);
      setTrackingPrompts(reviewed.items);
      setTrackingPendingReview(resolveTrackingPendingReview({ version: 1, items: reviewed.items }));
      trackPromptDraftRef.current = null;
      if (action === "confirm") {
        trackUndoRef.current = [];
        trackRedoRef.current = [];
        syncTrackingHistoryCounts();
      }
      const selected = reviewed.items.find((item) => item.parent_id === selectedTrackParent) ?? reviewed.items[0] ?? null;
      setSelectedTrackParent(selected?.parent_id ?? null);
      setSelectedTrackSubclass(selected?.subclasses[0]?.index ?? null);
      await loadSlice(index, undefined, { forceServer: true });
      setLabelsSummaryToken((value) => value + 1);
    } catch (error) {
      setTrackError(error instanceof Error ? error.message : `Could not ${action} Track preview`);
    } finally {
      setTrackReviewAction(null);
    }
  }, [changeTrackPromptTool, index, loadSlice, resolveTrackingPendingReview, reviewLocalTrackPreview, selectedTrackParent, syncTrackingHistoryCounts, taskId, trackReviewAction, trackingPendingReview]);

  useEffect(() => {
    // A parent/child/z change creates a new trackingPromptKey and therefore a
    // new discard callback. Always invalidate both visible and in-flight
    // proposals so a late response can never cross selection boundaries.
    discardTrackingProposal();
  }, [discardTrackingProposal]);

  const paintTrackingPrompt = useCallback((mask: Uint8Array, py: number, px: number, value: 0 | 1, radius: number) => {
    const [h, w] = shapeRef.current;
    const r2 = radius * radius;
    for (let y = Math.max(0, py - radius); y <= Math.min(h - 1, py + radius); y++) {
      for (let x = Math.max(0, px - radius); x <= Math.min(w - 1, px + radius); x++) {
        if ((y - py) ** 2 + (x - px) ** 2 <= r2) mask[y * w + x] = value;
      }
    }
  }, []);

  const renderTrackingPromptOverlay = useCallback(() => {
    const canvas = trackPromptCanvasRef.current;
    const [h, w] = shapeRef.current;
    if (!canvas || h === 0 || w === 0) return;
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    const selectedMask = currentTrackingPromptMask();
    const key = trackingPromptKey();
    const proposal = trackPromptProposalRef.current?.key === key
      ? trackPromptProposalRef.current.mask
      : null;
    const overlays: {
      mask: Uint8Array;
      color: [number, number, number];
      emphasis: 0 | 1 | 2;
    }[] = [];
    for (const prompt of trackingPrompts) {
      for (const child of prompt.subclasses) {
        const isSelectedChild = prompt.parent_id === selectedTrackParent && child.index === selectedTrackSubclass;
        const seed = child.seeds.find((item) => item.z === index);
        const mask = isSelectedChild
          ? selectedMask
          : seed && seed.shape[0] === h && seed.shape[1] === w
            ? maskFromTrackingSeed(seed.rle, h * w)
            : null;
        if (!mask?.some(Boolean)) continue;
        overlays.push({
          mask,
          color: trackingPromptColor(prompt.parent_id, child.index),
          emphasis: isSelectedChild ? 2 : prompt.parent_id === selectedTrackParent ? 1 : 0,
        });
      }
    }
    overlays.sort((a, b) => a.emphasis - b.emphasis);
    if (overlays.length || proposal) {
      const image = ctx.createImageData(w, h);
      for (const overlay of overlays) {
        compositeMaskColor(image, overlay.mask, overlay.color, overlay.emphasis === 2 ? 185 : overlay.emphasis === 1 ? 120 : 65);
      }
      // AI Box/Point prediction is a green proposal layer. It is deliberately
      // separate from the magenta durable child seed until Enter/double-click.
      if (proposal) compositeMaskColor(image, proposal, [34, 197, 94], 185);
      ctx.putImageData(image, 0, 0);
      const scale = canvas.getBoundingClientRect().width / Math.max(w, 1);
      for (const overlay of overlays) {
        const [r, g, b] = overlay.color;
        const width = overlay.emphasis === 2 ? 2.8 : overlay.emphasis === 1 ? 1.8 : 1.1;
        const contour = overlay.emphasis === 2 ? "#ffffff" : `rgba(${r}, ${g}, ${b}, ${overlay.emphasis === 1 ? 0.95 : 0.65})`;
        strokeMaskContour(ctx, overlay.mask, h, w, Math.max(0.75, width / Math.max(scale, 0.001)), contour);
      }
      if (proposal) strokeMaskContour(ctx, proposal, h, w, Math.max(1, 2.5 / Math.max(scale, 0.001)), "#86efac");
    }
    const scale = canvas.getBoundingClientRect().width / Math.max(w, 1);
    const line = Math.max(1, 2.5 / Math.max(scale, 0.001));
    const box = trackPromptBoxRef.current;
    if (box) {
      ctx.save(); ctx.strokeStyle = trackPromptTool === "box" ? "#f59e0b" : "#38bdf8"; ctx.lineWidth = line;
      ctx.strokeRect(Math.min(box.x0, box.x1), Math.min(box.y0, box.y1), Math.abs(box.x1 - box.x0), Math.abs(box.y1 - box.y0)); ctx.restore();
    }
    for (const point of trackPromptPointsRef.current.key === trackingPromptKey() ? trackPromptPointsRef.current.points : []) {
      ctx.beginPath(); ctx.arc(point.x, point.y, Math.max(2, 6 / Math.max(scale, 0.001)), 0, Math.PI * 2);
      ctx.fillStyle = point.label ? "#22c55e" : "#ef4444"; ctx.fill(); ctx.strokeStyle = "#ffffff"; ctx.lineWidth = line; ctx.stroke();
    }
    const hover = trackPromptHoverRef.current;
    if (hover && (trackPromptTool === "brush" || trackPromptTool === "erase")) {
      drawBrushCursor(ctx, hover[1], hover[0], trackPromptTool === "brush" ? trackPromptBrushSize : trackPromptEraserSize, trackPromptTool === "brush" ? "#f472b6" : "#38bdf8", line, 2 / Math.max(scale, 0.001), "disc");
    } else if (hover && (trackPromptTool === "box" || trackPromptTool === "box_erase")) {
      drawCrosshairCursor(ctx, hover[1], hover[0], w, h, trackPromptTool === "box" ? "#f59e0b" : "#38bdf8", line, 3 / Math.max(scale, 0.001));
    } else if (hover && trackPromptTool === "point") {
      const positive = trackPromptHoverLabelRef.current === 1;
      drawBrushCursor(ctx, hover[1], hover[0], Math.max(2, 6 / Math.max(scale, 0.001)), positive ? "#22c55e" : "#ef4444", line, 2 / Math.max(scale, 0.001), "disc");
    }
  }, [currentTrackingPromptMask, fitMode, index, selectedTrackParent, selectedTrackSubclass, trackPromptBrushSize, trackPromptEraserSize, trackPromptRevision, trackPromptTool, trackingPromptKey, trackingPrompts, zoom]);

  useEffect(() => { renderTrackingPromptOverlay(); }, [renderTrackingPromptOverlay, sliceLoading]);

  const clearTrackingSeed = useCallback(() => {
    const mask = currentTrackingPromptMask();
    if (!mask) return;
    discardTrackingProposal();
    mask.fill(0);
    trackPromptPointsRef.current = { key: trackingPromptKey(), points: [] };
    setTrackPromptRevision((v) => v + 1);
    void saveTrackingPromptMask(mask.slice());
  }, [currentTrackingPromptMask, discardTrackingProposal, saveTrackingPromptMask, trackingPromptKey]);

  const onTrackPromptPointerDown = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!trackPromptTool || tracking) return;
    const point = pixelFromEvent(e);
    const mask = currentTrackingPromptMask();
    if (!point || !mask) return;
    e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId);
    const [py, px] = point;
    if (trackPromptTool === "brush" || trackPromptTool === "erase") {
      trackPromptDrawingRef.current = true; trackPromptLastRef.current = point;
      paintTrackingPrompt(mask, py, px, trackPromptTool === "brush" ? 1 : 0, trackPromptTool === "brush" ? trackPromptBrushSize : trackPromptEraserSize);
      setTrackPromptRevision((v) => v + 1);
    } else if (trackPromptTool === "box" || trackPromptTool === "box_erase") {
      trackPromptDrawingRef.current = true; trackPromptBoxRef.current = { x0: px, y0: py, x1: px, y1: py };
      setTrackPromptRevision((v) => v + 1);
    } else {
      const key = trackingPromptKey();
      if (trackPromptPointsRef.current.key !== key) trackPromptPointsRef.current = { key, points: [] };
      const label = e.altKey ? 0 : 1;
      const points = [...trackPromptPointsRef.current.points, { x: px, y: py, label: label as 0 | 1 }];
      trackPromptPointsRef.current = { key, points };
      setTrackPromptRevision((v) => v + 1);
      const seq = ++trackPromptPredictSeqRef.current;
      trackPromptPredictingRef.current = true;
      trackPromptFinalizeWhenReadyRef.current = e.ctrlKey || e.metaKey;
      const prediction = predictMaskFromPoints(taskId, "z", index, points.map((p) => [p.x, p.y]), points.map((p) => p.label as 0 | 1), undefined, roiOnlyRef.current)
        .then((res) => {
          const predicted = Uint8Array.from(decodeRuns(res.runs, res.shape[0] * res.shape[1]), (v) => v ? 1 : 0);
          stageTrackingProposal(key, seq, predicted);
        })
        .catch((error) => {
          if (seq !== trackPromptPredictSeqRef.current) return;
          trackPromptPredictingRef.current = false;
          trackPromptFinalizeWhenReadyRef.current = false;
          setTrackError(error instanceof Error ? error.message : "Point prompt failed");
        });
      trackPromptPredictionPromiseRef.current = prediction;
      void prediction.finally(() => {
        if (trackPromptPredictionPromiseRef.current === prediction) trackPromptPredictionPromiseRef.current = null;
      });
    }
  }, [currentTrackingPromptMask, index, paintTrackingPrompt, pixelFromEvent, stageTrackingProposal, taskId, trackPromptBrushSize, trackPromptEraserSize, trackPromptTool, tracking, trackingPromptKey]);

  const onTrackPromptPointerMove = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    const point = pixelFromEvent(e);
    trackPromptHoverRef.current = point;
    trackPromptHoverLabelRef.current = e.altKey ? 0 : 1;
    const mask = currentTrackingPromptMask();
    if (point && mask && trackPromptDrawingRef.current && (trackPromptTool === "brush" || trackPromptTool === "erase")) {
      const last = trackPromptLastRef.current ?? point;
      const steps = Math.max(1, Math.ceil(Math.hypot(point[0] - last[0], point[1] - last[1])));
      for (let step = 1; step <= steps; step++) {
        const py = Math.round(last[0] + (point[0] - last[0]) * step / steps);
        const px = Math.round(last[1] + (point[1] - last[1]) * step / steps);
        paintTrackingPrompt(mask, py, px, trackPromptTool === "brush" ? 1 : 0, trackPromptTool === "brush" ? trackPromptBrushSize : trackPromptEraserSize);
      }
      trackPromptLastRef.current = point;
    } else if (point && trackPromptDrawingRef.current && trackPromptBoxRef.current) {
      trackPromptBoxRef.current = { ...trackPromptBoxRef.current, x1: point[1], y1: point[0] };
    }
    setTrackPromptRevision((v) => v + 1);
  }, [currentTrackingPromptMask, paintTrackingPrompt, pixelFromEvent, trackPromptBrushSize, trackPromptEraserSize, trackPromptTool]);

  const onTrackPromptPointerUp = useCallback(() => {
    if (!trackPromptDrawingRef.current) return;
    trackPromptDrawingRef.current = false; trackPromptLastRef.current = null;
    const mask = currentTrackingPromptMask();
    const box = trackPromptBoxRef.current;
    trackPromptBoxRef.current = null;
    if (!mask) return;
    if (trackPromptTool === "box_erase" && box) {
      const [h, w] = shapeRef.current;
      for (let y = Math.max(0, Math.floor(Math.min(box.y0, box.y1))); y <= Math.min(h - 1, Math.ceil(Math.max(box.y0, box.y1))); y++)
        for (let x = Math.max(0, Math.floor(Math.min(box.x0, box.x1))); x <= Math.min(w - 1, Math.ceil(Math.max(box.x0, box.x1))); x++) mask[y * w + x] = 0;
      void saveTrackingPromptMask(mask.slice());
    } else if (trackPromptTool === "box" && box) {
      const key = trackingPromptKey();
      // A double-click used to finalize an existing proposal also produces
      // click-sized pointer-up boxes. Do not replace the good proposal with a
      // meaningless zero-area prediction before the double-click event fires.
      if (Math.abs(box.x1 - box.x0) < 2 || Math.abs(box.y1 - box.y0) < 2) {
        setTrackPromptRevision((v) => v + 1);
        return;
      }
      const seq = ++trackPromptPredictSeqRef.current;
      trackPromptPredictingRef.current = true;
      trackPromptFinalizeWhenReadyRef.current = false;
      const prediction = predictMaskFromBox(taskId, "z", index, [[box.x0, box.y0], [box.x1, box.y1]], undefined, roiOnlyRef.current)
        .then((res) => {
          const predicted = Uint8Array.from(decodeRuns(res.runs, res.shape[0] * res.shape[1]), (v) => v ? 1 : 0);
          stageTrackingProposal(key, seq, predicted);
        })
        .catch((error) => {
          if (seq !== trackPromptPredictSeqRef.current) return;
          trackPromptPredictingRef.current = false;
          trackPromptFinalizeWhenReadyRef.current = false;
          setTrackError(error instanceof Error ? error.message : "Box prompt failed");
        });
      trackPromptPredictionPromiseRef.current = prediction;
      void prediction.finally(() => {
        if (trackPromptPredictionPromiseRef.current === prediction) trackPromptPredictionPromiseRef.current = null;
      });
    } else {
      void saveTrackingPromptMask(mask.slice());
    }
    setTrackPromptRevision((v) => v + 1);
  }, [currentTrackingPromptMask, index, saveTrackingPromptMask, stageTrackingProposal, taskId, trackPromptTool, trackingPromptKey]);

  const onTrackPromptPointerCancel = useCallback(() => {
    trackPromptDrawingRef.current = false; trackPromptLastRef.current = null; trackPromptBoxRef.current = null;
    setTrackPromptRevision((v) => v + 1);
  }, []);

  const jump = useCallback(
    (delta: number) => requestIndex(index + delta),
    [requestIndex, index],
  );

  const jumpToZ = useCallback(
    (z: number) => requestIndex(z),
    [requestIndex],
  );


  /** Arrow keys pan image position; they never change the current layer. */
  const panViewport = useCallback((direction: "left" | "right" | "up" | "down") => {
    const vp = viewportRef.current;
    if (!vp) return;
    // A manual pan supersedes delayed open/Fit centering so it stays put.
    needsOpenCenterRef.current = false;
    pendingFitRef.current = null;
    zoomAnchorRef.current = null;
    if (direction === "left" || direction === "right") {
      panCanvasHorizontally(vp, direction === "left" ? -1 : 1);
    } else {
      panCanvasVertically(vp, direction === "up" ? -1 : 1);
    }
    if (cmdScrollLockRef.current) {
      cmdScrollLockRef.current = { left: vp.scrollLeft, top: vp.scrollTop };
    }
  }, []);

  const handleLifecycleAction = useCallback(
    async (labelId: number, action: LabelLifecycleAction) => {
      setLifecycleError(null);
      try {
        if (action === "reject") {
          const result = await planDeleteLabel(
            taskId,
            labelId,
            axisRef.current,
            pendingToolSlices(),
          );
          return await applyPendingToolPlan(result.axis, result.slices);
        }
        await setLabelLifecycle(taskId, labelId, action);
        setLabelsSummaryToken((v) => v + 1);
        return true;
      } catch (e) {
        setLifecycleError(e instanceof Error ? e.message : `Failed to ${action} label ${labelId}`);
        return false;
      }
    },
    [taskId, applyPendingToolPlan, pendingToolSlices],
  );

  const runDeleteActiveNow = useCallback(async () => {
    const labelId = activeIdRef.current;
    if (deleteRunning || labelId < 1) return;
    // The summary describes what the *server* holds, so on its own it rejects a
    // label the user just painted and has not saved yet — the same staleness
    // trap `runMergeLabelsNow` documents ("a preflight must never suppress the
    // actual request"). Delete plans overlay the unsaved buffers, so accept
    // anything present there too. The guard still avoids raising a destructive
    // confirm for an id that exists nowhere at all.
    const inSummary = labelsSummaryRows.some((row) => row.id === labelId);
    const inPending =
      idsRef.current?.some((v) => v === labelId) ||
      pendingSlicesRef.current
        .values()
        .some((ids) => ids.some((v) => v === labelId));
    if (!inSummary && !inPending) {
      window.alert(`Label ${labelId} does not exist.`);
      return;
    }
    if (
      !window.confirm(
        `Delete label ${labelId}? This removes every voxel of this label from the whole volume.`,
      )
    ) {
      return;
    }
    setDeleteRunning(true);
    const deleted = await handleLifecycleAction(labelId, "reject");
    if (!deleted) window.alert(`Could not delete label ${labelId}.`);
    setDeleteRunning(false);
  }, [deleteRunning, labelsSummaryRows, handleLifecycleAction]);

  const toggleHidden = useCallback((id: number) => {
    setHiddenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleSolo = useCallback((id: number) => {
    setSoloId((prev) => (prev === id ? null : id));
  }, []);

  const resetVisibility = useCallback(() => {
    setHiddenIds(new Set());
    setSoloId(null);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      // Undo/Redo mutate the raster the same way painting does, so they
      // must respect the swap guard too (#31 item 5) — otherwise Ctrl+Z
      // could revert a stroke from before the user swapped into 3D view
      // even though the Undo *button* is grayed out via the tool fieldset.
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (editable && !swapped) {
          if (trackPromptTool != null) redoTrackingPrompt();
          else redo();
        }
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (editable && !swapped) {
          if (trackPromptTool != null) undoTrackingPrompt();
          else undo();
        }
        return;
      }
      // Per-user tool shortcuts: Cmd+letter on macOS, Ctrl+letter elsewhere,
      // from the profile map (`accounts/shortcuts.py`). Checked before the
      // pass-through below, which is what otherwise handed every modified key
      // straight to the browser. `preventDefault` is required: several of the
      // defaults (Cmd/Ctrl+F, +P, +B) are browser or OS bindings, and a tool
      // switch that also opened Find would be worse than no shortcut.
      if (editable && !swapped) {
        const shortcutTool = toolForShortcut(e, annotateShortcuts);
        if (
          shortcutTool
          && !(shortcutTool === "interpolate" && !interpolationEnabled)
          && !(shortcutTool === "flood_fill" && !floodFillEnabled)
        ) {
          e.preventDefault();
          if (shortcutTool === "verify") handleLifecycleAction(activeId, "verify");
          else if (shortcutTool === "solo") toggleSolo(activeId);
          else setPaintTool(shortcutTool);
          return;
        }
      }
      // Let Cmd/Ctrl+C/V/A/D/… reach the browser (copy/paste/select-all).
      if (e.ctrlKey || e.metaKey) return;
      // A/D change layer. Arrow keys pan image position without changing it.
      if (e.key === "a") {
        e.preventDefault();
        jump(-1);
      } else if (e.key === "d") {
        e.preventDefault();
        jump(1);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        panViewport("left");
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        panViewport("right");
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        panViewport("up");
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        panViewport("down");
      }
      // Annotate-only hotkeys below.
      else if (!editable || swapped) {
        /* z-nav only in View */
      } else if (e.key === "Enter" && (trackPromptTool === "box" || trackPromptTool === "point")) {
        e.preventDefault();
        void commitTrackingProposal();
      } else if (e.key === "Escape" && trackPromptTool != null) {
        e.preventDefault();
        discardTrackingProposal();
      } else if (e.key === "v") setPaintTool("select");
      else if (e.key === "b") setPaintTool("brush");
      else if (e.key === "e") setPaintTool("eraser");
      else if (e.key === "p") setPaintTool("point_mask");
      else if (e.key === "m") setPaintTool("box_mask");
      else if (e.key === "o") setPaintTool("boundary");
      else if (e.key === "r") setPaintTool("box_eraser");
      else if (e.key === "t") setPaintTool("seeds"); // #29 item U7 (Cellable: T = watershed_3d)
      else if (e.key === "i" && interpolationEnabled) setPaintTool("interpolate");
      else if (e.key === "l" && floodFillEnabled) setPaintTool("flood_fill");
      else if (e.key === "c") setPaintTool("split_3d");
      else if (e.key === "g") setPaintTool("merge");
      // Label-lifecycle/visibility hotkeys (#29 items U9/U10/U11/U12) — all
      // operate on the currently active label, matching what the Filters
      // Options buttons already do; checked via `e.key.toLowerCase()` +
      // `e.shiftKey` (not the raw shifted `e.key`) so caps-lock can't
      // silently swap which one fires.
      else if (!e.shiftKey && e.key.toLowerCase() === "f") {
        // F = Verify (V stays Select — #29 item U9, do not remap V).
        handleLifecycleAction(activeId, "verify");
      } else if (e.key === "Delete") {
        // Delete = Reject. A keystroke must never skip the same confirmation
        // used by the destructive Delete paths (#29 item U10).
        if (window.confirm(`Reject label ${activeId}? This deletes every voxel of this label from the whole volume.`)) {
          handleLifecycleAction(activeId, "reject");
        }
      } else if (!e.shiftKey && e.key.toLowerCase() === "h") {
        setHideVerified((v) => !v); // #29 item U11
      } else if (!e.shiftKey && e.key.toLowerCase() === "s") {
        toggleSolo(activeId); // #29 item U12
      } else if (e.shiftKey && e.key.toLowerCase() === "s") {
        resetVisibility(); // #29 item U12 ("show all")
      } else if (e.key === "Enter" && AI_POINT_TOOLS.includes(paintTool)) {
        // Point/Boundary: re-predict committed-only, then commit that —
        // never whatever the last hover frame happened to show (#27 item
        // L4, Cellable's `finalise()` semantics).
        finalizeAiPoints();
      } else if (e.key === "Enter" && paintTool === "box_mask") {
        if (hasAiPreview) commitAiPreview();
      } else if (e.key === "Escape" && AI_PREVIEW_TOOLS.includes(paintTool)) {
        // Esc clears the AI proposal on ALL AI tools, including Box (#25
        // item C.2) — clearAiPoints also cancels an in-progress Box
        // rubber-band drag.
        clearAiPoints();
      } else if (e.key === "Enter" && paintTool === "interpolate") {
        void runInterpolation();
      } else if (e.key === "Escape" && contextMenu) {
        setContextMenu(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    editable,
    swapped,
    undo,
    redo,
    jump,
    panViewport,
    paintTool,
    hasAiPreview,
    commitAiPreview,
    finalizeAiPoints,
    clearAiPoints,
    interpolationEnabled,
    floodFillEnabled,
    runInterpolation,
    activeId,
    handleLifecycleAction,
    toggleSolo,
    resetVisibility,
    contextMenu,
    trackPromptTool,
    commitTrackingProposal,
    discardTrackingProposal,
    undoTrackingPrompt,
    redoTrackingPrompt,
    annotateShortcuts,
  ]);

  // While Cmd/Ctrl is held: lock scroll so trackpad cannot pan mid-zoom.
  useEffect(() => {
    const syncLock = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      const vp = viewportRef.current;
      if (mod && vp) {
        if (!cmdScrollLockRef.current) {
          cmdScrollLockRef.current = { left: vp.scrollLeft, top: vp.scrollTop };
        }
      } else {
        cmdScrollLockRef.current = null;
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Meta" || e.key === "Control") syncLock(e);
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key === "Meta" || e.key === "Control") syncLock(e);
      // keyup on Meta clears e.metaKey before this fires in some browsers
      if ((e.key === "Meta" || e.key === "Control") && !e.metaKey && !e.ctrlKey) {
        cmdScrollLockRef.current = null;
      }
    };
    const onBlur = () => {
      cmdScrollLockRef.current = null;
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
    };
  }, []);

  // Native wheel (non-passive) so preventDefault actually blocks scroll while Cmd held.
  // Re-bind when the viewport mounts (it is absent during the loading shell).
  useEffect(() => {
    if (meta.loading || meta.error || !meta.data) return;
    const vp = viewportRef.current;
    if (!vp) return;

    const onScroll = () => {
      const lock = cmdScrollLockRef.current;
      if (!lock) return;
      if (vp.scrollLeft !== lock.left) vp.scrollLeft = lock.left;
      if (vp.scrollTop !== lock.top) vp.scrollTop = lock.top;
    };

    const onWheel = (e: WheelEvent) => {
      if (drawingRef.current) return;
      if (!(e.ctrlKey || e.metaKey)) {
        // Plain wheel pans — treat as user view adjustment.
        if (needsOpenCenterRef.current && (e.deltaX !== 0 || e.deltaY !== 0)) {
          needsOpenCenterRef.current = false;
        }
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      needsOpenCenterRef.current = false;

      // Freeze pan for this Cmd gesture; zoom toward the cursor so the image
      // does not appear to crawl upward (freezing raw pixels alone does that).
      const layout = stageLayoutRef.current;
      const factor = Math.pow(WHEEL_ZOOM_BASE, -e.deltaY / 100);
      if (layout && layout.stageW > 0) {
        const rect = vp.getBoundingClientRect();
        const offsetX = e.clientX - rect.left;
        const offsetY = e.clientY - rect.top;
        const sx = vp.scrollLeft + offsetX;
        const sy = vp.scrollTop + offsetY;
        zoomAnchorRef.current = {
          offsetX,
          offsetY,
          localX: sx - layout.stageLeft,
          localY: sy - layout.stageTop,
          oldW: layout.stageW,
        };
      }
      // Keep scroll locked at the pre-event position until layout applies the anchor.
      cmdScrollLockRef.current = { left: vp.scrollLeft, top: vp.scrollTop };
      setZoom((z) => clampZoom(z * factor));
    };

    vp.addEventListener("scroll", onScroll);
    vp.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      vp.removeEventListener("scroll", onScroll);
      vp.removeEventListener("wheel", onWheel);
    };
  }, [meta.loading, meta.error, meta.data]);

  /** Scroll so the stage matches Fit window (centered) or Fit width (top-pinned when tall). */
  const applyFitScroll = useCallback(
    (
      vp: HTMLDivElement,
      layout: {
        stageW: number;
        stageH: number;
        stageLeft: number;
        stageTop: number;
      },
      mode: "window" | "width",
    ) => {
      const left = layout.stageLeft - (vp.clientWidth - layout.stageW) / 2;
      const top =
        mode === "width" && layout.stageH > vp.clientHeight + 0.5
          ? layout.stageTop
          : layout.stageTop - (vp.clientHeight - layout.stageH) / 2;
      vp.scrollLeft = left;
      vp.scrollTop = top;
    },
    [],
  );

  /** True when any part of the image stage intersects the visible viewport. */
  const stageIntersectsViewport = useCallback(
    (
      vp: HTMLDivElement,
      layout: {
        stageW: number;
        stageH: number;
        stageLeft: number;
        stageTop: number;
      },
    ) => {
      const viewLeft = vp.scrollLeft;
      const viewTop = vp.scrollTop;
      const viewRight = viewLeft + vp.clientWidth;
      const viewBottom = viewTop + vp.clientHeight;
      return (
        layout.stageLeft + layout.stageW > viewLeft &&
        layout.stageLeft < viewRight &&
        layout.stageTop + layout.stageH > viewTop &&
        layout.stageTop < viewBottom
      );
    },
    [],
  );

  // Stage size = frozen fit-base × zoom. Recomputing the fit base on every
  // zoom step amplified tiny shell/subpixel changes when zoomed in deep and
  // felt like the canvas was auto-adjusting — freeze the baseline instead.
  const layoutStage = useCallback(() => {
    const shell = shellRef.current;
    const img = imgRef.current;
    const vp = viewportRef.current;
    if (!shell || !img?.naturalWidth || !img.naturalHeight) return;
    const vw = shell.clientWidth;
    const vh = shell.clientHeight;
    if (vw <= 0 || vh <= 0) return;

    const prevLayout = stageLayoutRef.current;
    // Before a shell-driven refit, remember which stage point sits under the
    // viewport center so we can keep pan stable (otherwise padX/padY rebuild
    // leaves scroll on the black padding and the canvas looks empty until Fit).
    let preserveCenter: {
      localX: number;
      localY: number;
      offsetX: number;
      offsetY: number;
      oldStageW: number;
      oldStageH: number;
    } | null = null;
    if (
      vp &&
      prevLayout &&
      fitBaseRef.current.w > 0 &&
      !needsOpenCenterRef.current &&
      pendingFitRef.current == null &&
      !zoomAnchorRef.current
    ) {
      const offsetX = vp.clientWidth / 2;
      const offsetY = vp.clientHeight / 2;
      preserveCenter = {
        offsetX,
        offsetY,
        localX: vp.scrollLeft + offsetX - prevLayout.stageLeft,
        localY: vp.scrollTop + offsetY - prevLayout.stageTop,
        oldStageW: prevLayout.stageW,
        oldStageH: prevLayout.stageH,
      };
    }

    const shellChanged =
      Math.abs(lastShellRef.current.w - vw) > 1 ||
      Math.abs(lastShellRef.current.h - vh) > 1;
    // Never refit mid-zoom-anchor (would fight cursor anchoring).
    const allowShellRefit = shellChanged && !zoomAnchorRef.current;
    const firstFit = fitBaseRef.current.w <= 0;
    const mustRefit =
      pendingFitRef.current != null ||
      needsOpenCenterRef.current ||
      firstFit ||
      allowShellRefit;

    if (mustRefit) {
      const nw = img.naturalWidth;
      const nh = img.naturalHeight;
      let fitW: number;
      let fitH: number;
      if (fitMode === "width") {
        fitW = vw;
        fitH = nh * (vw / nw);
      } else {
        const s = Math.min(vw / nw, vh / nh);
        fitW = nw * s;
        fitH = nh * s;
      }
      fitBaseRef.current = {
        w: fitW,
        h: fitH,
        padX: vw / 2,
        padY: vh / 2,
      };
      lastShellRef.current = { w: vw, h: vh };
    }

    const { w: fitW, h: fitH, padX, padY } = fitBaseRef.current;
    const next = {
      stageW: fitW * zoom,
      stageH: fitH * zoom,
      contentW: fitW * zoom + padX * 2,
      contentH: fitH * zoom + padY * 2,
      stageLeft: padX,
      stageTop: padY,
    };

    const shouldFit =
      pendingFitRef.current != null || needsOpenCenterRef.current || firstFit;
    const fitModeForScroll = pendingFitRef.current ?? fitMode;
    justForcedCenterRef.current = false;
    if (shouldFit) {
      flushSync(() => {
        setStageLayout(next);
      });
      const wasPendingFit = pendingFitRef.current != null;
      pendingFitRef.current = null;
      justForcedCenterRef.current = true;
      if (vp) {
        applyFitScroll(vp, next, fitModeForScroll);
        // Annotate chrome can still be settling (rails/panels). Re-center for
        // a couple of frames; only then drop needsOpenCenter — never again
        // unless the user clicks Fit.
        const shellW = vw;
        const shellH = vh;
        requestAnimationFrame(() => {
          const vp2 = viewportRef.current;
          const layout2 = stageLayoutRef.current;
          const shell2 = shellRef.current;
          if (!vp2 || !layout2) return;
          applyFitScroll(vp2, layout2, fitModeForScroll);
          requestAnimationFrame(() => {
            const shell3 = shellRef.current;
            const vp3 = viewportRef.current;
            const layout3 = stageLayoutRef.current;
            if (!shell3 || !vp3 || !layout3) return;
            const settled =
              Math.abs(shell3.clientWidth - shellW) <= 1 &&
              Math.abs(shell3.clientHeight - shellH) <= 1 &&
              (!shell2 ||
                (Math.abs(shell2.clientWidth - shellW) <= 1 &&
                  Math.abs(shell2.clientHeight - shellH) <= 1));
            applyFitScroll(vp3, layout3, fitModeForScroll);
            if (wasPendingFit || settled || stageIntersectsViewport(vp3, layout3)) {
              needsOpenCenterRef.current = false;
            }
            // If not settled, leave needsOpenCenter true — ResizeObserver will
            // refit+recenter once the side rails finish resizing the shell.
          });
        });
      } else if (wasPendingFit) {
        needsOpenCenterRef.current = false;
      }
    } else {
      flushSync(() => {
        setStageLayout((prev) => {
          if (
            prev &&
            Math.abs(prev.stageW - next.stageW) < 0.5 &&
            Math.abs(prev.stageH - next.stageH) < 0.5 &&
            Math.abs(prev.stageLeft - next.stageLeft) < 0.5 &&
            Math.abs(prev.stageTop - next.stageTop) < 0.5 &&
            Math.abs(prev.contentW - next.contentW) < 0.5 &&
            Math.abs(prev.contentH - next.contentH) < 0.5
          ) {
            return prev;
          }
          return next;
        });
      });
      // Shell resize rebuilt pad/stage without a Fit request — keep the same
      // image point under the viewport center (A/D and ◀/▶ must not drift).
      if (vp && preserveCenter && allowShellRefit) {
        const factorX =
          preserveCenter.oldStageW > 0
            ? next.stageW / preserveCenter.oldStageW
            : 1;
        const factorY =
          preserveCenter.oldStageH > 0
            ? next.stageH / preserveCenter.oldStageH
            : 1;
        vp.scrollLeft =
          next.stageLeft + preserveCenter.localX * factorX - preserveCenter.offsetX;
        vp.scrollTop =
          next.stageTop + preserveCenter.localY * factorY - preserveCenter.offsetY;
      }
      if (vp && !stageIntersectsViewport(vp, next)) {
        // Recover from a stuck black pad view without waiting for a manual Fit.
        applyFitScroll(vp, next, fitMode);
        justForcedCenterRef.current = true;
      }
    }
  }, [zoom, fitMode, fitEpoch, applyFitScroll, stageIntersectsViewport]);

  const requestFit = useCallback((mode: "window" | "width") => {
    zoomAnchorRef.current = null;
    cmdScrollLockRef.current = null;
    needsOpenCenterRef.current = false;
    pendingFitRef.current = mode;
    setFitMode(mode);
    setZoom(1);
    setFitEpoch((e) => e + 1);
  }, []);

  useLayoutEffect(() => {
    layoutStage();
    const shell = shellRef.current;
    if (!shell) return;
    const ro = new ResizeObserver(() => layoutStage());
    ro.observe(shell);
    return () => ro.disconnect();
  }, [layoutStage]);

  useLayoutEffect(() => {
    const vp = viewportRef.current;
    const layout = stageLayout;
    if (!vp || !layout) return;

    const anchor = zoomAnchorRef.current;
    if (anchor && anchor.oldW > 0) {
      zoomAnchorRef.current = null;
      pendingFitRef.current = null;
      needsOpenCenterRef.current = false;
      const factor = layout.stageW / anchor.oldW;
      const left = layout.stageLeft + anchor.localX * factor - anchor.offsetX;
      const top = layout.stageTop + anchor.localY * factor - anchor.offsetY;
      vp.scrollLeft = left;
      vp.scrollTop = top;
      if (cmdScrollLockRef.current) {
        cmdScrollLockRef.current = { left: vp.scrollLeft, top: vp.scrollTop };
      }
    }
  }, [stageLayout]);

  /** Full-height side rail: drag to resize, click (no drag) to collapse/expand. */
  const beginSideRail = useCallback(
    (side: "left" | "right", e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.stopPropagation();
      const handle = e.currentTarget;
      handle.setPointerCapture(e.pointerId);
      const startX = e.clientX;
      const startW = side === "left" ? leftPanelW : rightPanelW;
      const open = side === "left" ? leftPanelOpen : rightPanelOpen;
      let dragged = false;
      const onMove = (ev: PointerEvent) => {
        const dx = ev.clientX - startX;
        if (!dragged && Math.abs(dx) < 4) return;
        dragged = true;
        if (side === "left") {
          if (!open) setLeftPanelOpen(true);
          setLeftPanelW(clampSidePanel(open ? startW + dx : SIDE_PANEL_DEFAULT + dx));
        } else {
          if (!open) setRightPanelOpen(true);
          setRightPanelW(clampSidePanel(open ? startW - dx : SIDE_PANEL_DEFAULT - dx));
        }
      };
      const onUp = (ev: PointerEvent) => {
        handle.releasePointerCapture(ev.pointerId);
        handle.removeEventListener("pointermove", onMove);
        handle.removeEventListener("pointerup", onUp);
        if (!dragged) {
          if (side === "left") setLeftPanelOpen((v) => !v);
          else setRightPanelOpen((v) => !v);
        }
      };
      handle.addEventListener("pointermove", onMove);
      handle.addEventListener("pointerup", onUp);
    },
    [leftPanelW, rightPanelW, leftPanelOpen, rightPanelOpen],
  );

  const gridLeftW = leftPanelOpen ? leftPanelW : 0;
  const gridRightW = rightPanelOpen ? rightPanelW : 0;

  /** +/- zoom toward viewport center; does not change z. */
  const applyZoom = useCallback(
    (nextRaw: number) => {
      const vp = viewportRef.current;
      const layout = stageLayoutRef.current;
      const next = clampZoom(nextRaw);
      if (next === zoom) return;
      needsOpenCenterRef.current = false;
      if (vp && layout && layout.stageW > 0) {
        const offsetX = vp.clientWidth / 2;
        const offsetY = vp.clientHeight / 2;
        const sx = vp.scrollLeft + offsetX;
        const sy = vp.scrollTop + offsetY;
        zoomAnchorRef.current = {
          offsetX,
          offsetY,
          localX: sx - layout.stageLeft,
          localY: sy - layout.stageTop,
          oldW: layout.stageW,
        };
      }
      setZoom(next);
    },
    [zoom],
  );

  /** The smallest id nothing is using — counting saved voxels, unsaved planes
   * and Track-only parent prompts, but *not* the current Active reservation,
   * so clicking New twice without painting keeps returning the same id. */
  const newInstance = useCallback(() => {
    const planes: (Int32Array | null)[] = [idsRef.current];
    for (const ids of pendingSlicesRef.current.values()) planes.push(ids);
    const next = nextFreshLabelId({
      summaryIds: labelsSummaryRows.map((row) => row.id),
      trackParentIds: trackingPrompts.map((prompt) => prompt.parent_id),
      planes,
    });
    // `nextIdRef` is the server's "never reuse below this" bookkeeping for
    // tools that mint ids themselves (Split, Track). New no longer consults it
    // — a hole below it is exactly what New is now for — but it must still
    // never go backwards.
    nextIdRef.current = Math.max(nextIdRef.current, next + 1);
    setActiveId(next);
  }, [labelsSummaryRows, trackingPrompts]);

  const togglePinned3D = useCallback((id: number) => {
    setPinned3D((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    // Labels-section 3D click — refresh meshes.
    setLabels3DRefreshKey((v) => v + 1);
  }, []);

  /** Bulk-pin labels into the 3D view (This slice / All one-click). Adds to
   * the existing pin set — does not clear other pins. Always refreshes 3D
   * even when every id was already pinned (re-click after Save). */
  const pinManyTo3D = useCallback((ids: number[]) => {
    if (ids.length === 0) return;
    setPinned3D((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (id > 0) next.add(id);
      }
      return next;
    });
    setLabels3DRefreshKey((v) => v + 1);
  }, []);

  // --- What the 3D panel loads, and what merely changes what it *draws* ----
  //
  // Coupling rule (03 item B3 — "don't over-hook unrelated features"):
  //   * **Rebuild 3D** only when the 3D pin set changes, or an explicit 3D
  //     action fires (`labels3DRefreshKey`: pin toggle, 3D slice / 3D all).
  //     Meshing a label server-side is the most expensive call in the app.
  //   * **Never rebuild** for 2D-only visibility work. Solo / Hide / Hide
  //     Verified drive the canvas overlay, and at most toggle `mesh.visible`
  //     on geometry that is already loaded — no refetch, no re-mesh.
  // Solo therefore no longer *narrows* the 3D view (it used to, which made
  // every ○/◉ click a full 3D reload); the pin column is what 3D follows.
  const label3DIds = useMemo(() => {
    const ids: number[] = [];
    for (const id of pinned3D) {
      if (id > 0) ids.push(id);
    }
    ids.sort((a, b) => a - b);
    return ids;
  }, [pinned3D]);

  // Client-side visibility for already-loaded meshes (see above).
  const hidden3DIds = useMemo(() => {
    const hidden = new Set<number>();
    for (const id of pinned3D) {
      if (soloId != null && soloId > 0 && id !== soloId) hidden.add(id);
      else if (hiddenIds.has(id)) hidden.add(id);
      else if (hideVerified && verifiedIds.has(id)) hidden.add(id);
      // Same membership rule as Region only (an instance that reaches the ROI
      // anywhere is in), and the same caution: an id the summary has never
      // heard of is unsaved work, not a label proven to be outside the ROI.
      else if (
        hideOutsideRegion &&
        roiVolumeIds &&
        !roiVolumeIds.has(id) &&
        labelsSummaryRows.some((row) => row.id === id)
      ) {
        hidden.add(id);
      }
    }
    return hidden;
  }, [
    pinned3D,
    soloId,
    hiddenIds,
    hideVerified,
    verifiedIds,
    hideOutsideRegion,
    roiVolumeIds,
    labelsSummaryRows,
  ]);

  const filter = displayFilter(brightness, contrast);
  // Whole-instance filtering has taken over for this plane, so the pixel clip
  // must come off — leaving it on would cut the very instances the filter
  // decided to show back off at the ROI boundary.
  const regionFilterReady =
    roiOnly && regionMaskUrl != null && regionMaskBitsUrl === regionMaskUrl;
  const cursor = useMemo(
    () => canvasCursorForTool(paintTool, { editable, swapped }),
    [editable, swapped, paintTool],
  );

  // z / zoom inputs — wide enough for deep volumes and 2000% zoom.
  const zInputWidthCh = Math.max(5, String(axisLen).length + 1);
  const zoomInputWidthCh = Math.max(5, String(Math.round(MAX_ZOOM * 100)).length + 1);

  if (meta.loading) return <p className="muted">Loading volume…</p>;
  if (meta.error) return <div className="error">{meta.error}</div>;
  if (!meta.data) return null;

  return (
    <div className="canvas-root">
      {shareStage && (
        <div className="share-modal-backdrop" onClick={closeShareModal}>
          <div
            className="share-modal"
            role="dialog"
            aria-label="Record hard case"
            onClick={(e) => e.stopPropagation()}
          >
            {shareStage === "confirm" ? (
              <>
                <h3>Share this label with everyone on this project?</h3>
                <p className="muted" style={{ fontSize: "0.82rem" }}>
                  Label <strong>#{activeId}</strong> will appear in the
                  project&rsquo;s Hard Cases for its manager, its requester, and
                  every annotator working on it. You and managers can annotate
                  it or take it down later; everyone else sees it View-only.
                </p>
              </>
            ) : shareError ? (
              <>
                <h3>Couldn&rsquo;t record this hard case</h3>
                <p className="error">{shareError}</p>
              </>
            ) : (
              <>
                <h3>Hard case recorded</h3>
                <p className="muted" style={{ fontSize: "0.82rem" }}>
                  Label <strong>#{shareCase?.label_id}</strong> is now on{" "}
                  <strong>{shareCase?.project_title || "this project"}</strong>
                  &rsquo;s Hard Cases. Optionally copy the public link below to
                  paste outside the app &mdash; anyone with it can view the case
                  read-only, <strong>no account needed</strong>.
                </p>
                <div className="share-modal-url">
                  <input
                    type="text"
                    readOnly
                    value={shareUrl ?? ""}
                    onFocus={(e) => e.currentTarget.select()}
                  />
                  <button type="button" onClick={() => void copyShareUrl()}>
                    {copyState === "copied" ? "Copied" : "Copy"}
                  </button>
                </div>
                {copyState === "failed" && (
                  <p className="share-modal-copy-status error" aria-live="polite">
                    Couldn&apos;t reach the clipboard — select the link above and copy it manually.
                  </p>
                )}
              </>
            )}
            <div className="share-modal-actions">
              {shareStage === "confirm" ? (
                <>
                  <button
                    type="button"
                    className="secondary"
                    onClick={closeShareModal}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => void confirmHardCase()}
                    disabled={sharing}
                  >
                    {sharing ? "Recording…" : "Share with the project"}
                  </button>
                </>
              ) : (
                <>
                  {shareCase && (
                    <Link to={shareCase.app_url}>
                      <button type="button" className="secondary">
                        Open in Hard Cases
                      </button>
                    </Link>
                  )}
                  <button
                    type="button"
                    className="secondary"
                    onClick={closeShareModal}
                  >
                    Close
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}
      {editable && (
        <AnnotateToolChrome
          disabled={
            swapped ||
            status === "saving" ||
            wsRunning ||
            splitRunning ||
            mergeRunning ||
            deleteRunning ||
            floodRunning ||
            tracking ||
            trackingPendingReview != null ||
            trackPromptTool != null
          }
          paintTool={paintTool}
          onPaintTool={setPaintTool}
          dirty={dirty}
          status={status}
          sliceLoading={sliceLoading}
          undoCount={undoCount}
          redoCount={redoCount}
          onSave={() => {
            void saveLabels("manual");
          }}
          onShare={openHardCaseConfirm}
          canShare={canShareActive}
          sharing={sharing}
          onUndo={undo}
          onRedo={redo}
          onDeleteSlice={deleteSlice}
          onResetLabels={() => void resetLabelsToRegistered()}
          resetRunning={resetRunning}
          brushSize={brushSize}
          onBrushSize={setBrushSize}
          eraserSize={eraserSize}
          onEraserSize={setEraserSize}
          cursorStyle={cursorStyle}
          onCursorStyle={changeCursorStyle}
          activeId={activeId}
          onActiveId={setActiveId}
          onNewInstance={newInstance}
          aiError={aiError}
          aiPointCount={aiPointCount}
          hasAiPreview={hasAiPreview}
          onFinalizeAiPoints={finalizeAiPoints}
          onCommitAiPreview={commitAiPreview}
          onClearAiPoints={clearAiPoints}
          wsTargetLabel={wsTargetLabel}
          wsSeedCount={wsSeeds.length}
          wsRunning={wsRunning}
          onClearWsSeeds={clearWsSeeds}
          onRunWatershed={runWatershedNow}
          interpolationEnabled={interpolationEnabled}
          floodFillEnabled={floodFillEnabled}
          overwriteMode={overwriteMode}
          onOverwriteMode={setOverwriteMode}
          floodDepth={floodDepth}
          onFloodDepth={(value) => setFloodDepth(value % 2 === 0 ? Math.max(1, value - 1) : value)}
          floodRunning={floodRunning}
          interpFirst={interpFirst}
          interpLast={interpLast}
          onInterpFirst={setInterpFirst}
          onInterpLast={setInterpLast}
          interpRunning={interpRunning}
          onInterpRun={() => void runInterpolation()}
          axisLabel={axisShortLabel(axis)}
          currentIndex={index}
          splitRunning={splitRunning}
          onSplitActive={() => void runSplitComponentsNow()}
          mergeIdA={mergeIdA}
          mergeIdB={mergeIdB}
          onMergeIdA={setMergeIdA}
          onMergeIdB={setMergeIdB}
          mergeRunning={mergeRunning}
          onMergeLabels={() => void runMergeLabelsNow()}
          deleteRunning={deleteRunning}
          onDeleteActive={() => void runDeleteActiveNow()}
        />
      )}

      <div
        className="canvas-main-row"
        data-swapped={swapped ? "true" : "false"}
        data-mode={editable ? "annotate" : "view"}
        style={{
          gridTemplateColumns: editable
            ? `${gridLeftW}px ${SIDE_RAIL_W}px minmax(0, 1fr) ${SIDE_RAIL_W}px ${gridRightW}px`
            : `minmax(0, 1fr) ${SIDE_RAIL_W}px ${gridRightW}px`,
        }}
      >
        {editable && (
          <TrackRail
            hidden={!leftPanelOpen}
            axisIsZ={axis === "z"}
            disabled={
              swapped ||
              status === "saving" ||
              wsRunning ||
              splitRunning ||
              mergeRunning ||
              deleteRunning ||
              tracking ||
              trackPromptHistoryBusy
            }
            activeId={activeId}
            activeColorCss={labelColorCss(activeId)}
            tracking={tracking}
            trackingParentIds={trackingParentIds}
            promptEditing={trackPromptTool != null}
            promptTool={trackPromptTool}
            savingProgress={trackProgressSaving}
            progressSaved={trackProgressSaved}
            promptBrushSize={trackPromptBrushSize}
            promptEraserSize={trackPromptEraserSize}
            trackError={trackError}
            prompts={trackingPrompts}
            pendingReview={trackingPendingReview}
            reviewAction={trackReviewAction}
            promptUndoCount={trackUndoCount}
            promptRedoCount={trackRedoCount}
            overwriteMode={trackOverwriteMode}
            selectedParentId={selectedTrackParent}
            selectedChildIndex={selectedTrackSubclass}
            onSelectPrompt={selectTrackingPrompt}
            onSelectChild={setSelectedTrackSubclass}
            onQueueActive={() => void queueActiveTrackingPrompt()}
            onAddChild={() => void addTrackingSubclass()}
            onPromptTool={changeTrackPromptTool}
            onSaveProgress={() => void saveTrackProgress()}
            onPromptBrushSize={setTrackPromptBrushSize}
            onPromptEraserSize={setTrackPromptEraserSize}
            onClearSeed={clearTrackingSeed}
            onRemoveChild={(subclassIndex) => void removeTrackingSubclass(subclassIndex)}
            onRemovePrompt={() => void removeTrackingPrompt()}
            onPromptUndo={undoTrackingPrompt}
            onPromptRedo={redoTrackingPrompt}
            onOverwriteMode={setTrackOverwriteMode}
            onPropagateAll={() => void propagateTrackingQueue(false)}
            onPropagateSelected={() => void propagateTrackingQueue(true)}
            onReview={(action) => void reviewTrackPreview(action)}
          />
        )}

        {editable && (
          <div
            className={`side-rail side-rail-left${leftPanelOpen ? "" : " side-rail-collapsed"}`}
            title={leftPanelOpen ? "Drag to resize · click to hide Track" : "Click to show Track · drag to open"}
            onPointerDown={(e) => beginSideRail("left", e)}
          />
        )}

        {/* 2D canvas — layout-size zoom; status lives outside the scrollport. */}
        <div className="card canvas-panel">
          <div className="row spread labels-3d-header">
            <h3 style={{ margin: 0 }}>Canvas</h3>
            <span className="muted labels-3d-status">
              {!editable || swapped ? "View only" : `${instances.length} label(s) on slice`}
            </span>
            <button
              type="button"
              className="secondary labels-3d-swap"
              title={
                swapped
                  ? "Swap back — restore the 2D canvas to the center"
                  : "Swap — enlarge 3D Labels"
              }
              onClick={() => setSwapped((v) => !v)}
            >
              Swap
            </button>
          </div>
          <div ref={shellRef} className="canvas-viewport-shell">
            <div ref={viewportRef} className="canvas-viewport">
              <div
                className="canvas-scroll-content"
                style={
                  stageLayout
                    ? { width: stageLayout.contentW, height: stageLayout.contentH }
                    : undefined
                }
              >
                <div
                  className="canvas-stage"
                  style={
                    stageLayout
                      ? {
                          width: stageLayout.stageW,
                          height: stageLayout.stageH,
                          left: stageLayout.stageLeft,
                          top: stageLayout.stageTop,
                        }
                      : undefined
                  }
                >
                  {/* eslint-disable-next-line jsx-a11y/alt-text */}
                  <img
                    ref={imgRef}
                    onLoad={() => {
                      updateIntensityCanvas();
                      // Preserve pan across slice swaps: capture scroll before
                      // layout may rebuild stage metrics, then restore unless
                      // layout intentionally fit-centered (open / black recovery).
                      const vp = viewportRef.current;
                      const keepPan =
                        vp &&
                        !needsOpenCenterRef.current &&
                        pendingFitRef.current == null &&
                        fitBaseRef.current.w > 0
                          ? { left: vp.scrollLeft, top: vp.scrollTop }
                          : null;
                      layoutStage();
                      if (vp && keepPan && !justForcedCenterRef.current) {
                        vp.scrollLeft = keepPan.left;
                        vp.scrollTop = keepPan.top;
                      }
                    }}
                    style={{
                      display: "block",
                      width: "100%",
                      height: "100%",
                      imageRendering: "pixelated",
                      filter,
                      userSelect: "none",
                    }}
                  />
                  {regionMaskUrl && (
                    <img
                      src={regionMaskUrl}
                      alt=""
                      aria-hidden="true"
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
                  <canvas
                    ref={overlayRef}
                    onPointerDown={onPointerDown}
                    onPointerMove={onPointerMove}
                    onPointerUp={onPointerUp}
                    onPointerCancel={onPointerCancel}
                    onLostPointerCapture={onPointerCancel}
                    onPointerLeave={onPointerLeave}
                    onDoubleClick={onDoubleClick}
                    onContextMenu={onContextMenu}
                    style={{
                      position: "absolute",
                      inset: 0,
                      width: "100%",
                      height: "100%",
                      imageRendering: "pixelated",
                      cursor,
                      touchAction: "none",
                      visibility: roiOnly && !regionMaskUrl ? "hidden" : undefined,
                      ...(roiOnly && regionMaskUrl && !regionFilterReady
                        ? {
                            maskImage: `url(${regionMaskUrl})`,
                            WebkitMaskImage: `url(${regionMaskUrl})`,
                            maskSize: "100% 100%",
                            WebkitMaskSize: "100% 100%",
                          }
                        : {}),
                    }}
                  />
                  {/* Unmasked hover-cursor layer — Region-only CSS mask on the
                      label overlay was clipping Flood fill / brush cursors so
                      the pointer appeared to vanish over the image. */}
                  <canvas
                    ref={cursorLayerRef}
                    aria-hidden="true"
                    style={{
                      position: "absolute",
                      inset: 0,
                      width: "100%",
                      height: "100%",
                      imageRendering: "pixelated",
                      pointerEvents: "none",
                    }}
                  />
                  <canvas
                    ref={trackPromptCanvasRef}
                    aria-label="SAM tracking prompt overlay"
                    onPointerDown={onTrackPromptPointerDown}
                    onPointerMove={onTrackPromptPointerMove}
                    onPointerUp={onTrackPromptPointerUp}
                    onPointerCancel={onTrackPromptPointerCancel}
                    onLostPointerCapture={onTrackPromptPointerCancel}
                    onPointerLeave={() => {
                      trackPromptHoverRef.current = null;
                      setTrackPromptRevision((v) => v + 1);
                    }}
                    onDoubleClick={(e) => {
                      if (trackPromptTool !== "box" && trackPromptTool !== "point") return;
                      e.preventDefault();
                      e.stopPropagation();
                      void commitTrackingProposal();
                    }}
                    onContextMenu={(e) => trackPromptTool && e.preventDefault()}
                    style={{
                      position: "absolute",
                      inset: 0,
                      width: "100%",
                      height: "100%",
                      imageRendering: "pixelated",
                      // When inactive this layer must not steal the annotate
                      // cursor (Flood fill etc.): leave cursor unset and keep
                      // pointer-events none so the overlay canvas below wins.
                      cursor: trackPromptTool ? "none" : undefined,
                      touchAction: "none",
                      pointerEvents: trackPromptTool ? "auto" : "none",
                    }}
                  />
                </div>
              </div>
            </div>
            <div className="canvas-status-overlay">
              <span ref={statusReadoutRef} />
            </div>
            {rendererNotice && (
              <div className="canvas-renderer-notice" role="status">
                {rendererNotice}
              </div>
            )}
            {swapped && (
              <div className="canvas-swap-overlay" aria-live="polite">
                {editable ? "View only — Swap to annotate" : "Swap to enlarge canvas"}
              </div>
            )}
          </div>
        </div>

        <div
          className={`side-rail side-rail-right${rightPanelOpen ? "" : " side-rail-collapsed"}`}
          title={
            rightPanelOpen
              ? "Drag to resize · click to hide 3D / Labels"
              : "Click to show 3D / Labels · drag to open"
          }
          onPointerDown={(e) => beginSideRail("right", e)}
        />

        <Labels3DPanel
          taskId={taskId}
          labelIds={label3DIds}
          refreshKey={labels3DRefreshKey}
          hiddenIds={hidden3DIds}
          swapped={swapped}
          onToggleSwap={() => setSwapped((v) => !v)}
          fetchMesh={api.fetchLabels3DMesh}
        />

        <div className="labels-panel-slot">
          <LabelsPanel
            scope={labelsScope}
            onScopeChange={setLabelsScope}
            activeId={activeId}
            onSetActiveId={setActiveId}
            sliceInstances={instances}
            rows={labelsSummaryRows}
            rowsLoading={labelsSummaryLoading}
            hiddenIds={hiddenIds}
            soloId={soloId}
            onToggleHidden={toggleHidden}
            onToggleSolo={toggleSolo}
            onResetVisibility={resetVisibility}
            pinnedIds={pinned3D}
            onTogglePinned={togglePinned3D}
            onPinMany={pinManyTo3D}
            onJumpToZ={jumpToZ}
            hideVerified={hideVerified}
            onHideVerifiedChange={setHideVerified}
            hasRegionMask={Boolean(meta.data?.has_region_mask)}
            hideOutsideRegion={hideOutsideRegion}
            onHideOutsideRegionChange={setHideOutsideRegion}
            regionMemberIds={roiVolumeIds}
            onLifecycleAction={handleLifecycleAction}
            onRefresh={() => {
              setLabelsSummaryToken((v) => v + 1);
              setRegionMembershipToken((v) => v + 1);
            }}
            readOnly={!editable}
            focusId={initialActiveId ?? null}
            pinActiveToTopToken={pinActiveToTopToken}
          />
          {editable && lifecycleError && (
            <p className="error labels-lifecycle-error">{lifecycleError}</p>
          )}
        </div>
      </div>

      <div className="canvas-controls">
        <div className="row canvas-toolrow" style={{ flexWrap: "wrap" }}>
          <button
            type="button"
            className="secondary"
            title="Previous layer (large step)"
            // Avoid focus-driven scroll-into-view moving the canvas position.
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => jump(-10)}
          >
            ◀◀
          </button>
          <button
            type="button"
            className="secondary"
            title="Previous layer"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => jump(-1)}
          >
            ◀
          </button>
          <input
            type="range"
            min={0}
            max={axisLen - 1}
            value={index}
            onChange={(e) => requestIndex(Number(e.target.value))}
            style={{ flex: 1, minWidth: 80, maxWidth: "none" }}
            title={`${axisShortLabel(axis)} ${index + 1}/${axisLen}`}
          />
          <button
            type="button"
            className="secondary"
            title="Next layer"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => jump(1)}
          >
            ▶
          </button>
          <button
            type="button"
            className="secondary"
            title="Next layer (large step)"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => jump(10)}
          >
            ▶▶
          </button>
          <span className="muted slice-index" style={{ whiteSpace: "nowrap" }}>
            {axisShortLabel(axis)}{" "}
            <CommitNumberInput
              value={index + 1}
              min={1}
              max={axisLen}
              title={`Go to ${axisShortLabel(axis)} layer (1–${axisLen})`}
              widthCh={zInputWidthCh}
              onCommit={(n) => requestIndex(n - 1)}
            />
            /{axisLen}
          </span>
          <button
            className="secondary"
            onClick={() => applyZoom(zoom / BUTTON_ZOOM_FACTOR)}
          >
            −
          </button>
          <CommitNumberInput
            value={Math.round(zoom * 100)}
            min={Math.round(MIN_ZOOM * 100)}
            max={Math.round(MAX_ZOOM * 100)}
            suffix="%"
            title="Zoom percent (50%–2000%)"
            widthCh={zoomInputWidthCh}
            onCommit={(pct) => applyZoom(pct / 100)}
          />
          <button
            className="secondary"
            onClick={() => applyZoom(zoom * BUTTON_ZOOM_FACTOR)}
          >
            +
          </button>
        </div>
        <DisplayKnobs
          trailing={
            <>
              <JumpToRegionButton
                volumeId={volumeId}
                axis={axis}
                index={index}
                hasRegion={Boolean(meta.data?.has_region_mask)}
                getRegionIndex={api.getRegionIndex}
                onJump={requestIndex}
                // `requestIndex` refuses to navigate while a save is in
                // flight, so the button says so rather than doing nothing.
                disabled={status === "saving"}
              />
              <button
                className={fitMode === "window" ? "" : "secondary"}
                title="Fit the whole layer inside the viewport"
                onClick={() => requestFit("window")}
              >
                Fit window
              </button>
              <button
                className={fitMode === "width" ? "" : "secondary"}
                title="Fill the viewport width; scroll vertically if needed"
                onClick={() => requestFit("width")}
              >
                Fit width
              </button>
            </>
          }
          brightness={brightness}
          contrast={contrast}
          onBrightness={setBrightness}
          onContrast={setContrast}
          labelOpacity={labelOpacity}
          onLabelOpacity={setLabelOpacity}
          regionOpacity={meta.data?.has_region_mask ? regionOpacity : undefined}
          onRegionOpacity={meta.data?.has_region_mask ? setRegionOpacity : undefined}
        />
      </div>
      {/* No permanent hotkey-hint footer here (#31 item 2 — deliberately
          removed; it was costing a full-width row without adding anything
          the status overlay + docs don't already cover). The full hotkey
          map lives in progress/development.md and
          progress/frontend/features/MODULE.md, not in the live chrome. The
          status readout itself moved to an absolute overlay inside
          `.canvas-viewport` above, so it no longer costs a row either. */}

      {editable && contextMenu && (
        <div
          ref={contextMenuRef}
          className="canvas-context-menu"
          style={{ position: "fixed", left: contextMenu.x, top: contextMenu.y }}
        >
          <button
            type="button"
            className="danger"
            title="Discard the current Point/Box/Boundary proposal without committing (Esc)"
            onClick={() => {
              clearAiPoints();
              setContextMenu(null);
            }}
          >
            Cancel
          </button>
          {CONTEXT_MENU_LAYOUT.map((tool, cell) => tool == null ? (
            <span key={`gap-${cell}`} className="canvas-context-gap" aria-hidden="true" />
          ) : (
            <button
              key={tool}
              className="secondary"
              disabled={
                (tool === "interpolate" && !interpolationEnabled) ||
                (tool === "flood_fill" && !floodFillEnabled)
              }
              onClick={() => {
                if (tool === "interpolate" && contextMenu.labelId != null) {
                  interpContextSelectionRef.current = true;
                  const layer = idsIndexRef.current;
                  if (layer != null) {
                    const next = applyInterpolateCanvasClick(
                      contextMenu.labelId,
                      layer,
                      interpAnchorRef.current,
                      interpFirst,
                      interpLast,
                    );
                    if (next) {
                      setActiveId(next.activeId);
                      interpAnchorRef.current = next.anchor;
                      setInterpFirst(next.interpFirst);
                      setInterpLast(next.interpLast);
                    }
                  }
                }
                setPaintTool(tool);
                setContextMenu(null);
              }}
            >
              {CONTEXT_MENU_LABELS[tool]}
            </button>
          ))}
          {contextMenu.labelId != null && (
            <>
              <hr />
              <button
                className="secondary"
                onClick={() => {
                  const id = contextMenu.labelId as number;
                  setActiveId(id);
                  handleLifecycleAction(id, "verify");
                  setContextMenu(null);
                }}
              >
                ✓ Verify label {contextMenu.labelId}
              </button>
              <button
                className="secondary"
                onClick={() => {
                  toggleSolo(contextMenu.labelId as number);
                  setContextMenu(null);
                }}
              >
                ○ Solo label {contextMenu.labelId}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
