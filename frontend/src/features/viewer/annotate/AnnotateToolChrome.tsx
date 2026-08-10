import type { PaintTool } from "./paintTools";
import { labelColorCss } from "../labelColor";
import type { OverwriteMode } from "../../../api/viewer";
import { displayLayer, parseLayerInput } from "../layerIndex";
import {
  BRUSH_MAX_SIZE,
  BRUSH_MIN_SIZE,
  CURSOR_STYLES,
  type BrushCursorStyle,
} from "../brushCursor";

/** Cursor look for Brush and Erase. One preference serves both tools — an
 * annotator who finds the filled disc too occluding on Brush finds it just as
 * occluding on Erase, and two settings to keep in sync would be noise. */
function CursorStyleSelect({
  value,
  onChange,
}: {
  value: BrushCursorStyle;
  onChange: (style: BrushCursorStyle) => void;
}) {
  return (
    <span className="tool-cursor-control">
      <label className="muted" htmlFor="tool-cursor-style">
        Cursor
      </label>
      <select
        id="tool-cursor-style"
        className="tool-cursor-select"
        value={value}
        title="How the brush/erase footprint is drawn on the canvas. Saved in this browser."
        onChange={(e) => onChange(e.target.value as BrushCursorStyle)}
      >
        {CURSOR_STYLES.map((style) => (
          <option key={style.value} value={style.value}>
            {style.label}
          </option>
        ))}
      </select>
    </span>
  );
}

export type AnnotateSaveStatus = "idle" | "dirty" | "saving" | "saved" | "error";

/**
 * Top annotate chrome: tool strip + fixed-height tool-context row.
 * Annotate-only — View mode does not mount this module.
 * Axis lives in the editor topbar (next to View/Annotate) so View can switch too.
 */
export default function AnnotateToolChrome({
  disabled,
  paintTool,
  onPaintTool,
  dirty,
  status,
  sliceLoading,
  undoCount,
  redoCount,
  onSave,
  onShare,
  canShare,
  sharing,
  onUndo,
  onRedo,
  onDeleteSlice,
  onResetLabels,
  resetRunning,
  brushSize,
  onBrushSize,
  eraserSize,
  onEraserSize,
  cursorStyle,
  onCursorStyle,
  activeId,
  onActiveId,
  onNewInstance,
  aiError,
  aiPointCount,
  hasAiPreview,
  onFinalizeAiPoints,
  onCommitAiPreview,
  onClearAiPoints,
  wsTargetLabel,
  wsSeedCount,
  wsRunning,
  onClearWsSeeds,
  onRunWatershed,
  interpolationEnabled,
  floodFillEnabled,
  overwriteMode,
  onOverwriteMode,
  floodDepth,
  onFloodDepth,
  floodRunning,
  interpFirst,
  interpLast,
  onInterpFirst,
  onInterpLast,
  interpRunning,
  onInterpRun,
  axisLabel,
  currentIndex,
  splitRunning,
  onSplitActive,
  mergeIdA,
  mergeIdB,
  onMergeIdA,
  onMergeIdB,
  mergeRunning,
  onMergeLabels,
  deleteRunning,
  onDeleteActive,
}: {
  disabled: boolean;
  paintTool: PaintTool;
  onPaintTool: (t: PaintTool) => void;
  dirty: boolean;
  status: AnnotateSaveStatus;
  sliceLoading: boolean;
  undoCount: number;
  redoCount: number;
  onSave: () => void;
  /** Opens the confirm step for recording the Active label as a hard case. */
  onShare: () => void;
  /** True only when Active id exists as a real label in the volume. */
  canShare: boolean;
  sharing: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onDeleteSlice: () => void;
  /** Restore the whole working mask to the volume's registered label. Rendered
   * at the end of the *second* chrome row so it sits directly below Delete
   * layer at the end of the first: the same kind of action at two scales (this
   * layer / the whole task), read as one column. Using the row that already
   * exists is what keeps the chrome the same height as before.
   * Confirmed in `AnnotationCanvas`, not here. */
  onResetLabels: () => void;
  resetRunning: boolean;
  brushSize: number;
  onBrushSize: (n: number) => void;
  eraserSize: number;
  onEraserSize: (n: number) => void;
  cursorStyle: BrushCursorStyle;
  onCursorStyle: (style: BrushCursorStyle) => void;
  activeId: number;
  onActiveId: (id: number) => void;
  onNewInstance: () => void;
  aiError: string | null;
  aiPointCount: number;
  hasAiPreview: boolean;
  onFinalizeAiPoints: () => void;
  onCommitAiPreview: () => void;
  onClearAiPoints: () => void;
  wsTargetLabel: number | null;
  wsSeedCount: number;
  wsRunning: boolean;
  onClearWsSeeds: () => void;
  onRunWatershed: () => void;
  /** FEATURE_INTERPOLATION (and FEATURE_ANNOTATION_OPS) on the server. False
   * hides the tool entirely rather than showing a button that 503s. */
  interpolationEnabled: boolean;
  floodFillEnabled: boolean;
  overwriteMode: OverwriteMode;
  onOverwriteMode: (mode: OverwriteMode) => void;
  floodDepth: number;
  onFloodDepth: (depth: number) => void;
  floodRunning: boolean;
  /** Endpoint slices the active label was painted on; null until marked. */
  interpFirst: number | null;
  interpLast: number | null;
  onInterpFirst: (index: number | null) => void;
  onInterpLast: (index: number | null) => void;
  interpRunning: boolean;
  onInterpRun: () => void;
  /** Short name of the axis being scrubbed ("z"/"y"/"x"), for the labels. */
  axisLabel: string;
  /** The slice currently open — what "Use current" marks as an endpoint. */
  currentIndex: number;
  splitRunning: boolean;
  onSplitActive: () => void;
  mergeIdA: number | null;
  mergeIdB: number | null;
  onMergeIdA: (id: number | null) => void;
  onMergeIdB: (id: number | null) => void;
  mergeRunning: boolean;
  onMergeLabels: () => void;
  deleteRunning: boolean;
  onDeleteActive: () => void;
}) {
  return (
    <fieldset className="tool-fieldset" disabled={disabled}>
      {/* Mode-select row — fixed height so switching tools never jumps the canvas. */}
      <div className="row canvas-toolrow tool-strip">
        <button
          className={paintTool === "select" ? "" : "secondary"}
          onClick={() => onPaintTool("select")}
          title="Pick the instance under the cursor (V)"
        >
          Select
        </button>
        <button
          className={paintTool === "brush" ? "" : "secondary"}
          onClick={() => onPaintTool("brush")}
          title="Paint the active instance (B)"
        >
          Brush
        </button>
        <button
          className={paintTool === "eraser" ? "" : "secondary"}
          onClick={() => onPaintTool("eraser")}
          title="Erase (circular) (E)"
        >
          Erase
        </button>
        <button
          className={paintTool === "box_eraser" ? "" : "secondary"}
          onClick={() => onPaintTool("box_eraser")}
          title="Drag a box to clear a region (R)"
        >
          Box Erase
        </button>
        <button
          className={paintTool === "box_mask" ? "" : "secondary"}
          onClick={() => onPaintTool("box_mask")}
          title="Box Mask (M)"
        >
          Box Mask
        </button>
        <button
          className={paintTool === "point_mask" ? "" : "secondary"}
          onClick={() => onPaintTool("point_mask")}
          title="Point Mask (P)"
        >
          Point Mask
        </button>
        <button
          className={paintTool === "boundary" ? "" : "secondary"}
          onClick={() => onPaintTool("boundary")}
          title="Boundary (O)"
        >
          Boundary
        </button>
        <button
          className={paintTool === "seeds" ? "" : "secondary"}
          onClick={() => onPaintTool("seeds")}
          title="Click seed points on one instance -> 3D watershed split (T)"
        >
          Seeds
        </button>
        {/* Hidden, not disabled, when the server flag is off — a dead button
            is indistinguishable from a broken one. */}
        {interpolationEnabled && (
          <button
            className={paintTool === "interpolate" ? "" : "secondary"}
            onClick={() => onPaintTool("interpolate")}
            title="Fill the active label between two layers you have already painted (I)"
          >
            Interpolate
          </button>
        )}
        {floodFillEnabled && (
          <button
            className={paintTool === "flood_fill" ? "" : "secondary"}
            onClick={() => onPaintTool("flood_fill")}
            title="Flood the connected region under the cursor (F)"
          >
            Flood fill
          </button>
        )}
        <button
          className={paintTool === "split_3d" ? "" : "secondary"}
          onClick={() => onPaintTool("split_3d")}
          title="Split unconnected 3D components of a label (C)"
        >
          Split
        </button>
        <button
          className={paintTool === "merge" ? "" : "secondary"}
          onClick={() => onPaintTool("merge")}
          title="Merge two labels into the smaller id (G)"
        >
          Merge
        </button>
        <button
          className={paintTool === "delete" ? "" : "secondary"}
          onClick={() => onPaintTool("delete")}
          title="Delete every voxel of the selected label"
        >
          Delete
        </button>
        <span className="spacer" />
        <span className="muted tool-strip-status">
          {status === "saving" && "Saving…"}
          {status === "dirty" && "Unsaved"}
          {status === "error" && "Save failed"}
          {status === "idle" && sliceLoading && "Loading…"}
        </span>
        <button
          type="button"
          className="share-hard-case-btn"
          onClick={onShare}
          disabled={sharing || !canShare}
          title={
            canShare
              ? "Record the Active label as a hard case for this project. Everyone on the project can view it; you and managers can annotate or take it down. A copyable public link is offered afterwards."
              : `Active id ${activeId} has no painted label yet — pick an existing label before recording a hard case.`
          }
        >
          {sharing ? "Recording…" : "Record hard case"}
        </button>
        <button
          type="button"
          onClick={onSave}
          disabled={!dirty || status === "saving"}
          title="Write every edited layer to the on-disk working mask. Edits stay in memory until you click Save."
        >
          Save
        </button>
        <button className="secondary" onClick={onUndo} disabled={undoCount === 0}>
          Undo
        </button>
        <button className="secondary" onClick={onRedo} disabled={redoCount === 0}>
          Redo
        </button>
        {/* Last in the row. Its whole-task counterpart, Reset labels, is last
            in the row *below* — see `.tool-tail-btn`, which reserves one width
            for both so they line up as a column without either row growing. */}
        <button className="secondary tool-tail-btn" onClick={onDeleteSlice} title="Clear every label from the layer on screen. Other layers are untouched.">
          Delete layer
        </button>
      </div>

      {/* Fixed-height context row — Active/New on the left (hidden for Split). */}
      <div className="row canvas-toolrow tool-context">
        {paintTool !== "seeds" &&
          paintTool !== "split_3d" &&
          paintTool !== "merge" &&
          paintTool !== "delete" && (
          <>
            <span className="muted">Active</span>
            <span
              className="active-label-swatch"
              style={{ background: labelColorCss(activeId) }}
            />
            <input
              type="number"
              min={1}
              value={activeId}
              onChange={(e) => onActiveId(Math.max(1, Number(e.target.value)))}
              style={{ width: 56 }}
              title="Active label id"
            />
            <button className="secondary" onClick={onNewInstance} title="Select the smallest label id nothing uses yet — counting unsaved paint and Track-only parents, so it fills holes left by Merge/Delete instead of always counting up (keeps current tool)">
              New
            </button>
          </>
        )}
        {(paintTool === "interpolate" || paintTool === "flood_fill") && (
          <span className="tool-overwrite-control">
            <label className="muted" htmlFor="tool-overwrite-policy">Overwrite</label>
            <select
              id="tool-overwrite-policy"
              className="tool-overwrite-select"
              value={overwriteMode}
              onChange={(e) => onOverwriteMode(e.target.value as OverwriteMode)}
              disabled={floodRunning || interpRunning}
            >
              <option value="overwrite_empty">Empty voxels only</option>
              <option value="overwrite_all">All voxels</option>
            </select>
          </span>
        )}
        {paintTool === "brush" && (
          <>
            <span className="muted">Brush size</span>
            <input
              type="range"
              min={BRUSH_MIN_SIZE}
              max={BRUSH_MAX_SIZE}
              value={brushSize}
              onChange={(e) => onBrushSize(Number(e.target.value))}
              title={`Brush size ${brushSize} (${brushSize === 1 ? "single pixel" : `${brushSize} px across`})`}
            />
            <CursorStyleSelect value={cursorStyle} onChange={onCursorStyle} />
          </>
        )}
        {paintTool === "eraser" && (
          <>
            <span className="muted">Erase size</span>
            <input
              type="range"
              min={BRUSH_MIN_SIZE}
              max={BRUSH_MAX_SIZE}
              value={eraserSize}
              onChange={(e) => onEraserSize(Number(e.target.value))}
              title={`Erase size ${eraserSize} (${eraserSize === 1 ? "single pixel" : `${eraserSize} px across`})`}
            />
            <CursorStyleSelect value={cursorStyle} onChange={onCursorStyle} />
          </>
        )}
        {(paintTool === "point_mask" || paintTool === "boundary") && (
          <>
            {aiError && <span className="error">{aiError}</span>}
            <button onClick={onFinalizeAiPoints} disabled={aiPointCount === 0}>
              Commit (Enter)
            </button>
            <button className="secondary" onClick={onClearAiPoints} disabled={aiPointCount === 0}>
              Clear (Esc)
            </button>
          </>
        )}
        {paintTool === "box_mask" && (
          <>
            {aiError && <span className="error">{aiError}</span>}
            <button onClick={onCommitAiPreview} disabled={!hasAiPreview}>
              Commit (Enter)
            </button>
            <button className="secondary" onClick={onClearAiPoints} disabled={!hasAiPreview}>
              Clear (Esc)
            </button>
          </>
        )}
        {paintTool === "seeds" && (
          <>
            <button className="secondary" onClick={onClearWsSeeds} disabled={wsSeedCount === 0}>
              Clear seeds
            </button>
            <button onClick={onRunWatershed} disabled={wsRunning || wsSeedCount === 0}>
              {wsRunning ? "Splitting…" : "Run Watershed"}
            </button>
            {wsTargetLabel != null && (
              <span className="muted seeds-status">
                Target label {wsTargetLabel} · {wsSeedCount} seed(s)
              </span>
            )}
          </>
        )}
        {paintTool === "interpolate" && (
          <>
            <input
              type="number"
              min={1}
              value={interpFirst == null ? "" : displayLayer(interpFirst)}
              placeholder="Start layer"
              onChange={(e) => onInterpFirst(parseLayerInput(e.target.value))}
              style={{ width: 88 }}
              title={`Start layer along ${axisLabel} (1-based)`}
            />
            <button
              className="secondary"
              onClick={() => onInterpFirst(currentIndex)}
              disabled={interpRunning}
              title="Use the layer currently open as the start endpoint"
            >
              Use current
            </button>
            <input
              type="number"
              min={1}
              value={interpLast == null ? "" : displayLayer(interpLast)}
              placeholder="End layer"
              onChange={(e) => onInterpLast(parseLayerInput(e.target.value))}
              style={{ width: 88 }}
              title={`End layer along ${axisLabel} (1-based)`}
            />
            <button
              className="secondary"
              onClick={() => onInterpLast(currentIndex)}
              disabled={interpRunning}
              title="Use the layer currently open as the end endpoint"
            >
              Use current
            </button>
            <button
              onClick={onInterpRun}
              disabled={
                interpRunning ||
                activeId < 1 ||
                interpFirst == null ||
                interpLast == null ||
                Math.abs(interpFirst - interpLast) < 2
              }
              title="Fill between the endpoints into unsaved edits. Undo reverses; Save writes to disk."
            >
              {interpRunning ? "Interpolating…" : "Interpolate"}
            </button>
          </>
        )}
        {paintTool === "flood_fill" && (
          <>
            <label className="muted" htmlFor="flood-depth">Depth (z)</label>
            <input
              id="flood-depth"
              type="number"
              min={1}
              max={31}
              step={2}
              value={floodDepth}
              onChange={(e) => onFloodDepth(Math.max(1, Math.min(31, Number(e.target.value) || 1)))}
              style={{ width: 64 }}
              disabled={axisLabel !== "z" || floodRunning}
            />
          </>
        )}
        {paintTool === "split_3d" && (
          <>
            <span className="muted">Active</span>
            <span
              className="active-label-swatch"
              style={{ background: labelColorCss(activeId) }}
            />
            <input
              type="number"
              min={1}
              value={activeId}
              onChange={(e) => onActiveId(Math.max(1, Number(e.target.value)))}
              style={{ width: 56 }}
              title="Label id to split"
            />
            <button onClick={onSplitActive} disabled={splitRunning || activeId < 1}>
              {splitRunning ? "Splitting…" : "Split"}
            </button>
          </>
        )}
        {paintTool === "merge" && (
          <>
            <span className="muted">Labels</span>
            {mergeIdA != null && (
              <span
                className="active-label-swatch"
                style={{ background: labelColorCss(mergeIdA) }}
              />
            )}
            <input
              type="number"
              min={1}
              value={mergeIdA ?? ""}
              placeholder="First"
              onChange={(e) => {
                const id = Number(e.target.value);
                onMergeIdA(Number.isInteger(id) && id > 0 ? id : null);
              }}
              style={{ width: 72 }}
              title="First label id"
            />
            <span className="muted">+</span>
            {mergeIdB != null && (
              <span
                className="active-label-swatch"
                style={{ background: labelColorCss(mergeIdB) }}
              />
            )}
            <input
              type="number"
              min={1}
              value={mergeIdB ?? ""}
              placeholder="Second"
              onChange={(e) => {
                const id = Number(e.target.value);
                onMergeIdB(Number.isInteger(id) && id > 0 ? id : null);
              }}
              style={{ width: 72 }}
              title="Second label id"
            />
            <button
              onClick={onMergeLabels}
              disabled={
                mergeRunning ||
                mergeIdA == null ||
                mergeIdB == null ||
                mergeIdA === mergeIdB
              }
            >
              {mergeRunning ? "Merging…" : "Merge"}
            </button>
          </>
        )}
        {paintTool === "delete" && (
          <>
            <span className="muted">Active</span>
            <span
              className="active-label-swatch"
              style={{ background: labelColorCss(activeId) }}
            />
            <input
              type="number"
              min={1}
              value={activeId}
              onChange={(e) => onActiveId(Math.max(1, Number(e.target.value)))}
              style={{ width: 56 }}
              title="Label id to delete"
            />
            <button
              className="danger"
              onClick={onDeleteActive}
              disabled={deleteRunning || activeId < 1}
            >
              {deleteRunning ? "Deleting…" : "Delete"}
            </button>
          </>
        )}
        {/* Pushed to the end of this row so it lands directly beneath Delete
            layer, which is the last thing in the row above. Both rows are the
            same width and both buttons reserve the same width, so the pair
            reads as one column — without either row getting taller. Unlike
            everything else here it is not tool-specific: it is always shown,
            because it acts on the task rather than on the current tool. */}
        <span className="spacer" />
        <button
          type="button"
          className="secondary danger-outline editor-reset-labels-btn tool-tail-btn"
          onClick={onResetLabels}
          disabled={resetRunning}
          title="Discard this task's whole working annotation and restore the volume's registered label mask. Affects every layer, saved and unsaved. The registered source file is never changed."
        >
          {resetRunning ? "Resetting…" : "Reset labels"}
        </button>
      </div>
    </fieldset>
  );
}
