// Slice-viewer + in-app annotation API.
//
// Slices are streamed as PNG from the backend slice-IO endpoints. Auth is a
// token header (not a cookie), so an <img src> cannot authenticate — we fetch
// each slice with the header and hand back an object URL. Volume SliceViewer
// keeps those URLs in a bounded LRU; task View/Annotate share AnnotationCanvas.

import { api } from "./client";
import {
  authedChunkEndpoints,
  type ChunkEndpoints,
} from "../features/chunks/chunkClient";

export interface VolumeMeta {
  shape: { z: number; y: number; x: number };
  dtype: string;
  axes: string[];
  has_label: boolean;
  has_region_mask: boolean;
  region_mask_coverage?: number | null;
  volume_id: number;
  ready_streaming?: boolean;
  /** The ROI streams independently of the image; either may be ready alone. */
  region_ready_streaming?: boolean;
  display_range: { lo: number; hi: number };
}

export type Axis = "z" | "y" | "x";

export interface SliceParams {
  axis: Axis;
  index: number;
}

export const getVolumeMeta = (volumeId: number) =>
  api.get<VolumeMeta>(`/volumes/${volumeId}/meta/`);

// No window/level in the request: the server normalises every slice against
// the volume-wide display range once, so a slice is fetched exactly once per
// (axis, index) no matter how brightness/contrast are adjusted afterwards —
// those are applied client-side (a canvas filter) with zero extra requests.
export function imageSlicePath(volumeId: number, p: SliceParams): string {
  const q = new URLSearchParams({ axis: p.axis, index: String(p.index) });
  return `/api/volumes/${volumeId}/slice/?${q.toString()}`;
}

// `regionOnly` renders only the instances that touch the region on this plane,
// and renders them *whole* — the server decides per instance, because a
// colorized PNG cannot be filtered by instance id once it reaches the browser.
// (`AnnotationCanvas` has the raw ids and does the same thing client-side.)
export function labelSlicePath(
  volumeId: number,
  axis: Axis,
  index: number,
  regionOnly = false,
): string {
  const q = new URLSearchParams({ axis, index: String(index) });
  if (regionOnly) q.set("region_only", "1");
  return `/api/volumes/${volumeId}/label-slice/?${q.toString()}`;
}

export function regionMaskSlicePath(volumeId: number, axis: Axis, index: number): string {
  const q = new URLSearchParams({ axis, index: String(index) });
  return `/api/volumes/${volumeId}/region-mask-slice/?${q.toString()}`;
}

/** Every plane of one axis that holds any region voxel.
 *
 * Asked once per (volume, axis) and cached for the session — see
 * `features/viewer/regionIndex.ts`. The whole list, rather than "the nearest
 * one to here", is what lets the viewer also answer "this plane already has
 * region, so Jump does nothing" without a round trip. */
export interface RegionIndex {
  axis: Axis;
  length: number;
  axis_length?: number;
  revision?: string;
  indices: number[];
}

export const getRegionIndex = (volumeId: number, axis: Axis) =>
  api.get<RegionIndex>(`/volumes/${volumeId}/region-index/?axis=${axis}`);

/** Volume-wide "Region only" membership.
 *
 * `has_region` false means the volume has no ROI at all, which is *not* the
 * same as an empty `ids` (an ROI nothing reaches into): the first must not
 * filter anything, the second must hide everything. */
export interface RegionLabelIds {
  has_region: boolean;
  ids: number[];
}

export const getRegionLabelIds = (taskId: number) =>
  api.get<RegionLabelIds>(`/tasks/${taskId}/region-label-ids/`);

/** Fetch a PNG slice with the auth header and return an object URL.
 *
 * Takes an optional ``signal`` so a caller can cancel it — the slice viewer
 * prefetches several MB-sized neighbours per navigation step, and without
 * cancellation, rapidly changing slices (e.g. a fast scrub, or switching
 * pages before older fetches finish) piles up dozens of in-flight requests
 * that outlive their relevance and starve the ones that are actually needed.
 */
export async function fetchObjectUrl(
  path: string,
  signal?: AbortSignal,
): Promise<string> {
  const blob = await api.blob(path, signal);
  return URL.createObjectURL(blob);
}

// --- Fork-aware SAM2 tracking ---------------------------------------------

export interface SeedInput {
  z: number;
  rle: [number, number][]; // [start, length] over the flattened HxW mask
  shape: [number, number];
}

/** One structured ambiguity the automatic tracking logic refused to guess at. */
export interface TrackWarning {
  code: "ambiguous_component_association" | "ambiguous_child_merge" | string;
  message: string;
  parent_id?: number;
  z?: number;
  contact_z?: number;
  branches?: number[];
  assigned_branch?: number;
  rival_branches?: number[];
  rival_components?: number[];
  metrics?: Record<string, unknown>;
}

/** An ephemeral branch the backend inferred from one parent's prompt geometry. */
export interface InferredBranch {
  branch_key: number;
  subclass_index: number;
  seed_zs: number[];
  components?: { z: number; component_index: number; area: number; centroid: [number, number] }[];
}

/** A confirmed child-into-child merge, in whole-volume z. */
export interface TrackMergeEvent {
  loser_branch: number;
  survivor_branch: number;
  contact_z: number;
  reason: string;
  metrics?: Record<string, unknown>;
}

export interface TrackResult {
  final_id: number;
  branch_ids: number[];
  group: {
    group_id: number;
    branch_ids: number[];
    final_id: number;
    seed_z: number | null;
    seed_zs?: number[];
    subclass_branch_ids?: Record<string, number>;
    /** Explicit inclusive propagation bounds, as the user chose them. */
    start_z?: number | null;
    end_z?: number | null;
    inferred_branches?: InferredBranch[];
    branch_provider_ids?: Record<string, number>;
    merge_events?: TrackMergeEvent[];
    /** `branch_key -> last layer that branch contributes to`. */
    terminated_at?: Record<string, number>;
    warnings?: TrackWarning[];
    dropped_components?: { z: number; area: number; reason: string }[];
  } | null;
  warnings?: TrackWarning[];
}

export type TrackingPromptStatus = "draft" | "ready" | "running" | "pending" | "done" | "error";

export interface TrackingSubclass {
  index: number;
  seeds: SeedInput[];
}

export interface TrackingPrompt {
  parent_id: number;
  subclasses: TrackingSubclass[];
  /**
   * Explicit, **inclusive** propagation bounds in 0-based API z. The Track rail
   * shows them as 1-based layer numbers, matching the viewer's z field. `null`
   * means the annotator has not chosen one yet, which blocks Propagate.
   *
   * These are never derived from the seed layers. `z_range` below is a
   * read-only mirror kept for queues saved before this schema.
   */
  start_z: number | null;
  end_z: number | null;
  z_range: [number, number];
  status: TrackingPromptStatus;
  note?: string;
}

export interface TrackingPromptQueue {
  version: number;
  items: TrackingPrompt[];
  pending_review?: { parent_ids: number[]; status: "pending_review" } | null;
}

export interface TrackBatchResult {
  results: TrackResult[];
  done: number;
  total: number;
  axis: Axis;
  slices: PlannedLabelSlice[];
  /** Every group's warnings, flattened and tagged with their `parent_id`. */
  warnings?: TrackWarning[];
}

export const trackTaskFork = (
  taskId: number,
  seeds: SeedInput[],
  zRange?: [number, number],
  parentId?: number,
  axis: Axis = "z",
  pendingSlices: PendingToolSlice[] = [],
) =>
  api.post<TrackBatchResult>(`/tasks/${taskId}/track/`, {
    seeds,
    z_range: zRange,
    parent_id: parentId,
    axis,
    pending_slices: pendingSlices,
  });

export const getTrackingPrompts = (taskId: number) =>
  api.get<TrackingPromptQueue>(`/tasks/${taskId}/track/prompts/`);

export const putTrackingPrompt = (taskId: number, prompt: TrackingPrompt) =>
  api.put<TrackingPrompt>(`/tasks/${taskId}/track/prompts/`, prompt);

export const replaceTrackingPrompts = (taskId: number, items: TrackingPrompt[]) =>
  api.post<TrackingPromptQueue>(`/tasks/${taskId}/track/prompts/`, { items });

export const deleteTrackingPrompt = (taskId: number, parentId: number) =>
  api.del<{ deleted: boolean }>(
    `/tasks/${taskId}/track/prompts/?parent_id=${parentId}`,
  );

export const trackTaskBatch = (
  taskId: number,
  parentIds: number[] | undefined,
  axis: Axis,
  pendingSlices: PendingToolSlice[],
  overwriteMode: OverwriteMode = "overwrite_empty",
) =>
  api.post<TrackBatchResult>(`/tasks/${taskId}/track/batch/`, {
    parent_ids: parentIds,
    axis,
    pending_slices: pendingSlices,
    overwrite_mode: overwriteMode,
  });

export const reviewTrackingPreview = (taskId: number, action: "confirm" | "reject") =>
  api.post<{ action: "confirm" | "reject"; parent_ids: number[]; items: TrackingPrompt[] }>(
    `/tasks/${taskId}/track/review/`,
    { action },
  );

// --- In-app label editor (raw instance ids, RLE over the wire) -------------
// A colorized PNG (labelSlicePath above) is fine for read-only viewing, but
// the editor needs the *raw* ids so it can hit-test under the cursor and
// paint/erase specific instances. Run-length encoding keeps the payload small
// even though a decoded slice (int32 per pixel) would not be.

export interface LabelIdsResponse {
  shape: [number, number];
  runs: [number, number][]; // [id, run-length], row-major
  revision?: string;
}

export interface LabelState {
  max_label_id: number;
  next_label_id: number;
  revision?: string;
}

export const getLabelState = (taskId: number) =>
  api.get<LabelState>(`/tasks/${taskId}/label-state/`);

export const getLabelIds = (taskId: number, axis: Axis, index: number, signal?: AbortSignal) =>
  api.get<LabelIdsResponse>(
    `/tasks/${taskId}/label-ids/?axis=${axis}&index=${index}`,
    signal,
  );

export const putLabelIds = (
  taskId: number,
  axis: Axis,
  index: number,
  shape: [number, number],
  runs: [number, number][],
  origin: "manual" | "ai" = "manual",
  roiOnly = false,
  expectedRevision = "",
) =>
  api.put<LabelState>(`/tasks/${taskId}/label-ids/`, {
    axis,
    index,
    shape,
    runs,
    origin,
    roi_only: roiOnly,
    expected_revision: expectedRevision,
  });

/** Decode row-major RLE runs into a flat Int32Array of instance ids. */
export function decodeRuns(runs: [number, number][], size: number): Int32Array {
  const out = new Int32Array(size);
  let pos = 0;
  for (const [id, count] of runs) {
    out.fill(id, pos, pos + count);
    pos += count;
  }
  return out;
}

/** Inverse of decodeRuns: flat instance ids -> row-major RLE runs. */
export function encodeRuns(ids: Int32Array | Uint32Array): [number, number][] {
  const runs: [number, number][] = [];
  if (ids.length === 0) return runs;
  let start = 0;
  for (let i = 1; i <= ids.length; i++) {
    if (i === ids.length || ids[i] !== ids[start]) {
      runs.push([ids[start], i - start]);
      start = i;
    }
  }
  return runs;
}

// --- Cellable-ported interactive AI tools (Point/Box/Boundary, Seeds) ------
// See progress/history/19-cellable-parity-annotator-brief.md +
// backend/annotation/cellable_port/. Point/Box/Boundary are read-only
// "preview a mask" calls (0/1 label-RLE, reusing decodeRuns above) — the
// caller merges the result into its already-loaded slice and commits
// through putLabelIds like a brush stroke. Watershed is the one call here
// that mutates the server-side working copy directly (like trackTaskFork),
// since it operates in 3D across the whole label volume, not one slice.

export interface MaskPrediction {
  shape: [number, number];
  runs: [number, number][];
}

// `signal` lets the caller drop a superseded predict (rapid clicks / a new
// box drag before the last one resolved) — see AnnotationCanvas.tsx's
// sequence-guarded predict handlers (progress/history/23-cellable-parity-
// ort-and-prompt-ux.md).

export const predictMaskFromPoints = (
  taskId: number,
  axis: Axis,
  index: number,
  points: [number, number][],
  pointLabels: (0 | 1)[],
  signal?: AbortSignal,
  roiOnly = false,
) =>
  api.post<MaskPrediction>(
    `/tasks/${taskId}/predict-mask/`,
    { axis, index, mode: "points", points, point_labels: pointLabels, roi_only: roiOnly },
    signal,
  );

export const predictMaskFromBox = (
  taskId: number,
  axis: Axis,
  index: number,
  box: [[number, number], [number, number]],
  signal?: AbortSignal,
  roiOnly = false,
) =>
  api.post<MaskPrediction>(
    `/tasks/${taskId}/predict-mask/`,
    { axis, index, mode: "box", box, roi_only: roiOnly },
    signal,
  );

export const predictBoundary = (
  taskId: number,
  axis: Axis,
  index: number,
  points: [number, number][],
  pointLabels: (0 | 1)[],
  signal?: AbortSignal,
  roiOnly = false,
) =>
  api.post<MaskPrediction>(
    `/tasks/${taskId}/predict-mask/`,
    { axis, index, mode: "boundary", points, point_labels: pointLabels, roi_only: roiOnly },
    signal,
  );

/** Pre-computes the EfficientSAM embedding for one slice so a following
 * Point/Box/Boundary predict is decoder-only — fire-and-forget from the
 * frontend (slice change, entering an AI tool, neighbor prefetch). Never
 * throws for "model unavailable" (`{warmed: false}`, HTTP 200) — only a
 * genuine network/abort failure rejects. */
export const warmEmbedding = (
  taskId: number,
  axis: Axis,
  index: number,
  signal?: AbortSignal,
  point?: [number, number],
) =>
  api.post<{ warmed: boolean }>(
    `/tasks/${taskId}/warm-embedding/`,
    { axis, index, ...(point ? { point } : {}) },
    signal,
  );

export interface WatershedSeed {
  z: number;
  y: number;
  x: number;
}

export interface WatershedResult {
  target_label: number;
  new_label_ids: number[];
  bbox: [number, number, number, number, number, number];
  axis: Axis;
  slices: PlannedLabelSlice[];
}

export interface PendingToolSlice {
  index: number;
  shape: [number, number];
  runs: [number, number][];
}

export interface PlannedLabelSlice extends PendingToolSlice {
  /** Exact pre-plan plane, so applying a server compute result never has to
   * refetch every changed slice just to build compound Undo history. */
  before_runs?: [number, number][];
}

export const runWatershed = (
  taskId: number,
  label: number,
  seeds: WatershedSeed[],
  axis: Axis,
  pendingSlices: PendingToolSlice[],
) => api.post<WatershedResult>(`/tasks/${taskId}/watershed/`, {
  label, seeds, axis, pending_slices: pendingSlices,
});

export interface SplitComponentsResult {
  target_label: number;
  new_label_ids: number[];
  bbox: [number, number, number, number, number, number];
  components_kept: number;
  voxels_cleared: number;
  axis: Axis;
  slices: PlannedLabelSlice[];
}

export interface ResetWorkingLabelsResult {
  reset: boolean;
  task: number;
  volume: number;
  /** Where the mask was restored from ("" when the volume registered no label). */
  restored_from: string;
  seeded_empty: boolean;
}

/** Throw away this task's working annotation and re-seed it from the volume's
 * *registered* label mask.
 *
 * Destructive to the draft, and to nothing else — the registered source is only
 * read. `confirm` is required by the server as well as by the UI: this is the
 * one call that can discard a day's painting, so a stray POST must not do it.
 * Every in-memory buffer (pending planes, undo history, Track queue) has to be
 * dropped by the caller afterwards, or the editor would keep showing work that
 * is no longer on disk. */
export const resetWorkingLabels = (taskId: number) =>
  api.post<ResetWorkingLabelsResult>(`/tasks/${taskId}/labels/reset/`, {
    confirm: true,
  });

/** Split a label into 3D connected components (Cellable Split Label). */
export const runSplitComponents = (
  taskId: number,
  label: number,
  axis: Axis,
  pendingSlices: PendingToolSlice[],
) => api.post<SplitComponentsResult>(`/tasks/${taskId}/split-components/`, {
  label, axis, pending_slices: pendingSlices,
});

export interface MergeLabelsResult {
  kept_label: number;
  removed_label: number;
  voxels_merged: number;
  axis: Axis;
  slices: PlannedLabelSlice[];
}

/** Merge two labels; the larger id is absorbed into the smaller. */
export const runMergeLabels = (
  taskId: number,
  a: number,
  b: number,
  axis: Axis,
  pendingSlices: PendingToolSlice[],
) => api.post<MergeLabelsResult>(`/tasks/${taskId}/merge-labels/`, {
  a, b, axis, pending_slices: pendingSlices,
});

export interface DeleteLabelPlanResult {
  label_id: number;
  voxels_deleted: number;
  axis: Axis;
  slices: PlannedLabelSlice[];
}

export const planDeleteLabel = (
  taskId: number,
  label: number,
  axis: Axis,
  pendingSlices: PendingToolSlice[],
) => api.post<DeleteLabelPlanResult>(`/tasks/${taskId}/delete-label-plan/`, {
  label, axis, pending_slices: pendingSlices,
});

// --- WEBKNOSSOS-style interpolation (ADR-006) ------------------------------
// Two calls, one endpoint: `preview` plans and returns the intermediate 0/1
// masks *without writing anything*, `apply` recomputes the same plan server-
// side and commits it as one undoable operation. That split is what makes
// "Interpolate -> preview -> confirm/cancel" possible; the client never sends
// voxels back, so a confirm cannot write geometry the algorithm didn't produce.
//
// Masks arrive in the same label-RLE shape as MaskPrediction (decodeRuns
// above) — one entry per intermediate slice, keyed by its absolute index.

export type OverwriteMode = "overwrite_empty" | "overwrite_all";

export interface InterpolationSlice {
  /** Absolute index along `axis`, not an offset from the first endpoint. */
  index: number;
  shape: [number, number];
  runs: [number, number][];
}

export interface InterpolationPreview {
  axis: Axis;
  first_index: number;
  last_index: number;
  label: number;
  depth: number;
  spacing: [number, number];
  overwrite_mode: OverwriteMode;
  algorithm: string;
  algorithm_version: number;
  voxels_changed: number;
  slices: InterpolationSlice[];
}

export interface InterpolationApplied {
  operation_id: string;
  seq: number;
  axis: Axis;
  first_index: number;
  last_index: number;
  label: number;
  depth: number;
  overwrite_mode: OverwriteMode;
  voxels_changed: number;
  slices_written: number[];
}

export interface InterpolationRequest {
  axis: Axis;
  firstIndex: number;
  lastIndex: number;
  label: number;
  overwriteMode?: OverwriteMode;
  roiOnly?: boolean;
  /** Optional unsaved endpoint label planes (RLE) so plan sees pending edits. */
  firstRuns?: [number, number][];
  lastRuns?: [number, number][];
  shape?: [number, number];
}

const interpolationBody = (req: InterpolationRequest) => ({
  axis: req.axis,
  first_index: req.firstIndex,
  last_index: req.lastIndex,
  label: req.label,
  ...(req.overwriteMode ? { overwrite_mode: req.overwriteMode } : {}),
  roi_only: Boolean(req.roiOnly),
  ...(req.firstRuns && req.lastRuns && req.shape
    ? { first_runs: req.firstRuns, last_runs: req.lastRuns, shape: req.shape }
    : {}),
});

/** Plan the intermediate slices. Writes nothing — safe to discard. */
export const planInterpolation = (
  taskId: number,
  req: InterpolationRequest,
  signal?: AbortSignal,
) =>
  api.post<InterpolationPreview>(
    `/tasks/${taskId}/interpolate/`,
    { ...interpolationBody(req), mode: "preview" },
    signal,
  );

/** Commit the plan as one undoable annotation operation.
 * `idempotencyKey` makes a retry after a lost response safe. */
export const applyInterpolation = (
  taskId: number,
  req: InterpolationRequest,
  idempotencyKey: string,
) =>
  api.post<InterpolationApplied>(`/tasks/${taskId}/interpolate/`, {
    ...interpolationBody(req),
    mode: "apply",
    idempotency_key: idempotencyKey,
  });

export interface FloodFillRequest {
  axis: Axis;
  index: number;
  row: number;
  col: number;
  label: number;
  depth?: number;
  overwriteMode: OverwriteMode;
  roiOnly?: boolean;
}

export interface FloodFillResult {
  tool: "flood_fill";
  label: number;
  overwrite_mode: OverwriteMode;
  bbox: [number, number, number, number, number, number];
  voxels_changed: number;
  warnings: string[];
  slices_written?: number[];
  operation_id?: string;
  seq?: number;
  slices?: InterpolationSlice[];
}

const floodBody = (req: FloodFillRequest) => ({
  axis: req.axis,
  index: req.index,
  row: req.row,
  col: req.col,
  label: req.label,
  depth: req.depth ?? 1,
  overwrite_mode: req.overwriteMode,
  roi_only: Boolean(req.roiOnly),
});

export const planFloodFill = (taskId: number, req: FloodFillRequest) =>
  api.post<FloodFillResult>(`/tasks/${taskId}/flood-fill/`, {
    ...floodBody(req), mode: "preview",
  });

export const applyFloodFill = (
  taskId: number, req: FloodFillRequest, idempotencyKey: string,
) => api.post<FloodFillResult>(`/tasks/${taskId}/flood-fill/`, {
  ...floodBody(req), mode: "apply", idempotency_key: idempotencyKey,
});

// --- Labels panel (Filters Options: state/origin/lifecycle) + 3D preview ---
// Cellable parity — see progress/history/21-cellable-parity-followups.md.
// LabelState/LabelOrigin mirror backend/annotation/cellable_port/label_state.py.

export type LabelLifecycleState = "proposed" | "edited" | "verified";
export type LabelOrigin = "ai" | "watershed" | "split" | "manual" | "tracking" | "unknown";

export interface LabelSummaryRow {
  id: number;
  voxel_count: number;
  z_start: number;
  z_end: number;
  state: LabelLifecycleState;
  origin: LabelOrigin;
  verified_at: string;
}

export interface LabelStats {
  total: number;
  proposed: number;
  edited: number;
  verified: number;
}

export const getLabelsSummary = (taskId: number) =>
  api.get<{ labels: LabelSummaryRow[]; stats: LabelStats }>(`/tasks/${taskId}/labels-summary/`);

export type LabelLifecycleAction = "verify" | "unverify" | "reject";

export const setLabelLifecycle = (taskId: number, labelId: number, action: LabelLifecycleAction) =>
  api.post<{ label_id: number; action: string; state: LabelLifecycleState | null; removed: boolean }>(
    `/tasks/${taskId}/labels/${labelId}/lifecycle/`,
    { action },
  );

// --- 3D Labels: iso-surface meshes -----------------------------------------
// What `Labels3DPanel` renders: marching-cubes surfaces built by
// `cellable_port/labels_3d.py`. See `_labels_3d_mesh_response` in
// backend/annotation/api.py for the wire format; every field is 4-byte
// aligned so the typed-array views below are zero-copy. POST (not GET) so
// "3D slice" / "3D all" with hundreds of ids can't overflow URL/header
// limits (HTTP 431 -> "Preview failed").
//
// The older voxel-grid endpoint (`labels-3d/`) still exists server-side for
// old clients, but nothing in this app requests it any more.

export interface LabelMesh {
  id: number;
  /** Interleaved (z, y, x) vertex positions, in whole-volume voxel units. */
  vertices: Float32Array;
  /** Triangle indices into `vertices`. */
  indices: Uint32Array;
}

export interface Labels3DMesh {
  meshes: LabelMesh[];
  /** Min corner + extent of all returned geometry, voxel units (z, y, x). */
  origin: [number, number, number];
  size: [number, number, number];
  /** Physical voxel size (z, y, x); (1,1,1) when the volume doesn't record it. */
  voxelSize: [number, number, number];
  /** Labels dropped because the request exceeded the server triangle budget. */
  truncated: number;
}

function decodeLabels3DMesh(buf: ArrayBuffer): Labels3DMesh {
  const view = new DataView(buf);
  const version = view.getUint32(0, true);
  if (version !== 1) throw new Error(`unsupported labels-3d-mesh version ${version}`);
  const numMeshes = view.getUint32(4, true);
  const truncated = view.getUint32(8, true);
  const f = (o: number) => view.getFloat32(o, true);
  const origin: [number, number, number] = [f(16), f(20), f(24)];
  const size: [number, number, number] = [f(28), f(32), f(36)];
  const voxelSize: [number, number, number] = [f(40), f(44), f(48)];
  const meshes: LabelMesh[] = [];
  let offset = 52;
  for (let i = 0; i < numMeshes; i++) {
    const id = view.getInt32(offset, true);
    const numVertices = view.getUint32(offset + 4, true);
    const numTriangles = view.getUint32(offset + 8, true);
    offset += 12;
    const vertices = new Float32Array(buf, offset, numVertices * 3);
    offset += numVertices * 12;
    const indices = new Uint32Array(buf, offset, numTriangles * 3);
    offset += numTriangles * 12;
    meshes.push({ id, vertices, indices });
  }
  return { meshes, origin, size, voxelSize, truncated };
}

export async function fetchLabels3DMesh(
  taskId: number,
  labelIds: number[],
  signal?: AbortSignal,
): Promise<Labels3DMesh> {
  return decodeLabels3DMesh(
    await api.postArrayBuffer(
      `/tasks/${taskId}/labels-3d-mesh/`,
      { labels: labelIds },
      signal,
    ),
  );
}

// --- Hard cases (project-scoped + the public token link) -------------------
// See progress/history/{02-share-hard-case,05-submit-people-hardcases}.md.
// Recording a case is authed (annotator/manager who can open Annotate) and it
// becomes visible to the whole project; *reading* one either needs project
// membership (`/hard-cases/…`, see api/hardCases.ts) or the unguessable token,
// so the public endpoints live under /public/hard-cases/.
//
// The read viewer (`AnnotationCanvas` + `Labels3DPanel`) takes a `ViewerReadApi`
// adapter so the SAME components can render either the authed task/volume APIs
// or the public token endpoints, without threading a token through every call.

export interface PublicHardCaseMeta extends VolumeMeta {
  task_id: number;
  label_id: number;
  z_start: number;
  z_end: number;
  volume_name: string;
  project_title: string;
  note: string;
}

const publicBase = (token: string) => `/public/hard-cases/${encodeURIComponent(token)}`;
const publicTaskBase = (token: string) => `/public/tasks/${encodeURIComponent(token)}`;

/** Fetch the shared-case identity + volume meta (no auth). */
export const getPublicHardCaseMeta = (token: string) =>
  api.get<PublicHardCaseMeta>(`${publicBase(token)}/meta/`);

/** The set of read-only viewer calls `AnnotationCanvas`/`Labels3DPanel` need.
 * The default (authed) implementation and the public (token) implementation
 * are interchangeable — that's what lets one canvas serve both. */
export interface ViewerReadApi {
  getVolumeMeta: (volumeId: number) => Promise<VolumeMeta>;
  /**
   * Where this surface mints chunk tokens and reads pyramid capabilities.
   *
   * Its presence is what tells `AnnotationCanvas` a chunk source may be
   * mounted at all. It used to test `api === authedViewerApi`, which meant
   * every share fell back to whole-plane PNGs even when the volume's pyramid
   * was built and ready — the identity check answered "is this the logged-in
   * API", when the question is "can this surface authorize a chunk read".
   */
  chunkEndpoints?: ChunkEndpoints;
  getLabelState: (taskId: number) => Promise<LabelState>;
  getLabelsSummary: (
    taskId: number,
  ) => Promise<{ labels: LabelSummaryRow[]; stats: LabelStats }>;
  getLabelIds: (
    taskId: number,
    axis: Axis,
    index: number,
    signal?: AbortSignal,
  ) => Promise<LabelIdsResponse>;
  imageSlicePath: (volumeId: number, p: SliceParams) => string;
  regionMaskSlicePath: (volumeId: number, p: SliceParams) => string;
  getRegionIndex: (volumeId: number, axis: Axis) => Promise<RegionIndex>;
  /** Instance ids that touch the volume's ROI *anywhere in z*.
   *
   * "Region only" and "Hide non-ROI labels" hide or show a whole instance, so
   * the per-plane overlap the canvas computes for itself is only half the
   * answer — it is what keeps freshly painted, unsaved work visible, and this
   * is what stops a long mito from disappearing on the planes where it happens
   * to sit outside the ROI. */
  getRegionLabelIds: (taskId: number) => Promise<RegionLabelIds>;
  fetchLabels3DMesh: (
    taskId: number,
    labelIds: number[],
    signal?: AbortSignal,
  ) => Promise<Labels3DMesh>;
}

/** The normal authed viewer API — the default for `AnnotationCanvas`. */
export const authedViewerApi: ViewerReadApi = {
  getVolumeMeta,
  chunkEndpoints: authedChunkEndpoints,
  getLabelState,
  getLabelsSummary,
  getLabelIds,
  imageSlicePath,
  regionMaskSlicePath: (volumeId, p) =>
    regionMaskSlicePath(volumeId, p.axis, p.index),
  getRegionIndex,
  getRegionLabelIds,
  fetchLabels3DMesh,
};

/** A `ViewerReadApi` backed by the public token endpoints (ids in the closure,
 * so the id arguments the canvas passes are ignored — the token identifies the
 * task/volume). No auth header is sent (the endpoints are `AllowAny`). */
export function publicHardCaseApi(token: string): ViewerReadApi {
  return publicViewerApi(publicBase(token));
}

/**
 * Chunk token + capabilities routes for a public share, mounted under the same
 * revocable `base` its slice endpoints use. No auth header: these are
 * `AllowAny` and gated entirely by the share token already in the path, and
 * sending a stale `Authorization` from some other tab's localStorage could only
 * turn a working public page into a 401.
 */
function shareChunkEndpoints(base: string): ChunkEndpoints {
  return {
    capabilitiesUrl: (_volumeId, layer) =>
      `/api${base}/chunks/capabilities/${!layer || layer === "image" ? "" : `?layer=${layer}`}`,
    tokenUrl: () => `/api${base}/chunks/token/`,
    headers: () => new Headers({ "Content-Type": "application/json" }),
  };
}

function publicViewerApi(base: string): ViewerReadApi {
  return {
    getVolumeMeta: () => api.get<VolumeMeta>(`${base}/meta/`),
    chunkEndpoints: shareChunkEndpoints(base),
    getLabelState: () => api.get<LabelState>(`${base}/label-state/`),
    getLabelsSummary: () =>
      api.get<{ labels: LabelSummaryRow[]; stats: LabelStats }>(`${base}/labels-summary/`),
    getLabelIds: (_taskId, axis, index, signal) =>
      api.get<LabelIdsResponse>(`${base}/label-ids/?axis=${axis}&index=${index}`, signal),
    imageSlicePath: (_volumeId, p) => {
      const q = new URLSearchParams({ axis: p.axis, index: String(p.index) });
      return `/api${base}/slice/?${q.toString()}`;
    },
    regionMaskSlicePath: (_volumeId, p) => {
      const q = new URLSearchParams({ axis: p.axis, index: String(p.index) });
      return `/api${base}/region-mask-slice/?${q.toString()}`;
    },
    getRegionIndex: (_volumeId, axis) =>
      api.get<RegionIndex>(`${base}/region-index/?axis=${axis}`),
    getRegionLabelIds: () => api.get<RegionLabelIds>(`${base}/region-label-ids/`),
    fetchLabels3DMesh: async (_taskId, labelIds, signal) => {
      const buffer = await api.postArrayBuffer(
        `${base}/labels-3d-mesh/`,
        { labels: labelIds },
        signal,
      );
      return decodeLabels3DMesh(buffer);
    },
  };
}

export interface PublicTaskShareMeta extends VolumeMeta {
  task_id: number;
  z_start: number;
  z_end: number;
  volume_name: string;
  project_title: string;
}

export const getPublicTaskShareMeta = (token: string) =>
  api.get<PublicTaskShareMeta>(`${publicTaskBase(token)}/meta/`);

export function publicTaskShareApi(token: string): ViewerReadApi {
  return publicViewerApi(publicTaskBase(token));
}

export function publicScopedShareApi(token: string, volumeId: number): ViewerReadApi {
  return publicViewerApi(`/public/shares/${encodeURIComponent(token)}/volumes/${volumeId}`);
}
