import { useEffect, useRef, useState } from "react";
import type { TrackingPrompt } from "../../../api/viewer";
import type { OverwriteMode } from "../../../api/viewer";

export type TrackingPromptTool = "brush" | "erase" | "box_erase" | "box" | "point";

// Two columns, read row by row: the manual pair first, then the two box
// gestures side by side (Box proposes, Box erase takes away), then Point.
// Box sits at row 2 col 1 so the drag-a-rectangle pair lines up vertically
// with the paint/erase pair above it instead of trailing the grid.
const PROMPT_TOOL_CELLS: ({ tool: TrackingPromptTool; label: string; title: string } | null)[] = [
  { tool: "brush", label: "Brush", title: "Paint a child-class tracking seed" },
  { tool: "erase", label: "Erase", title: "Erase part of the child-class tracking seed" },
  { tool: "box", label: "Box", title: "Drag to propose; Enter or double-click to commit the child seed" },
  { tool: "box_erase", label: "Box erase", title: "Drag a rectangle to erase tracking seed pixels" },
  { tool: "point", label: "Point", title: "Click positive / Alt-click negative; Enter or double-click to commit" },
  null,
];

/** Scrollable SAM2 parent-class queue. Child-class numbers are local UI ids. */
export default function TrackRail({
  hidden, disabled, activeId, activeColorCss,
  tracking, trackingParentIds, promptEditing, promptTool, promptBrushSize, promptEraserSize,
  savingProgress, progressSaved,
  trackError, axisIsZ, prompts, selectedParentId, selectedChildIndex,
  pendingReview, reviewAction, promptUndoCount, promptRedoCount,
  overwriteMode,
  onSelectPrompt, onSelectChild, onQueueActive,
  onAddChild, onPromptTool, onSaveProgress, onPromptBrushSize, onPromptEraserSize,
  onClearSeed, onRemoveChild, onRemovePrompt,
  onPromptUndo, onPromptRedo, onOverwriteMode, onPropagateAll, onPropagateSelected, onReview,
}: {
  hidden: boolean; disabled: boolean; activeId: number; activeColorCss: string;
  tracking: boolean; trackingParentIds: number[];
  promptEditing: boolean; promptTool: TrackingPromptTool | null;
  savingProgress: boolean; progressSaved: boolean;
  promptBrushSize: number; promptEraserSize: number; trackError: string | null;
  axisIsZ: boolean; prompts: TrackingPrompt[]; selectedParentId: number | null;
  selectedChildIndex: number | null;
  pendingReview: { parent_ids: number[]; status: "pending_review" } | null;
  reviewAction: "confirm" | "reject" | null;
  promptUndoCount: number; promptRedoCount: number;
  overwriteMode: OverwriteMode;
  onSelectPrompt: (parentId: number) => void;
  onSelectChild: (index: number) => void; onQueueActive: () => void;
  onAddChild: () => void; onPromptTool: (tool: TrackingPromptTool | null) => void;
  onSaveProgress: () => void;
  onPromptBrushSize: (size: number) => void; onPromptEraserSize: (size: number) => void;
  onClearSeed: () => void;
  onRemoveChild: (index: number) => void; onRemovePrompt: () => void;
  onPropagateAll: () => void;
  onPropagateSelected: () => void;
  onOverwriteMode: (mode: OverwriteMode) => void;
  onPromptUndo: () => void; onPromptRedo: () => void;
  onReview: (action: "confirm" | "reject") => void;
}) {
  const railRef = useRef<HTMLDivElement | null>(null);
  const blocked = disabled || !axisIsZ;
  const selected = prompts.find((p) => p.parent_id === selectedParentId) ?? null;
  const selectedChild = selected?.subclasses.find((child) => child.index === selectedChildIndex) ?? null;
  const readyCount = prompts.filter((p) => p.subclasses.some((child) => child.seeds.length)).length;
  const canEdit = Boolean(selected && selectedChild);
  const selectedSeedZs = selected?.subclasses.flatMap((child) => child.seeds.map((seed) => seed.z)) ?? [];
  const startLayer = selectedSeedZs.length ? Math.min(...selectedSeedZs) : null;
  const endLayer = selectedSeedZs.length ? Math.max(...selectedSeedZs) : null;
  const pendingParentIds = pendingReview?.parent_ids?.length
    ? pendingReview.parent_ids
    : prompts.filter((prompt) => prompt.status === "pending").map((prompt) => prompt.parent_id);
  const reviewing = pendingParentIds.length > 0;
  const reviewLocked = reviewing || reviewAction != null;
  const [trackingElapsed, setTrackingElapsed] = useState(0);

  useEffect(() => {
    if (!tracking) {
      setTrackingElapsed(0);
      return;
    }
    const started = Date.now();
    const update = () => setTrackingElapsed(Math.floor((Date.now() - started) / 1000));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [tracking]);

  // Track owns wheel input while the pointer is over its rail. Prefer the
  // nested queue when it can move; otherwise scroll the whole rail body.
  // A native non-passive listener makes this reliable even when a viewer or
  // page-level wheel handler is installed elsewhere.
  useEffect(() => {
    const rail = railRef.current;
    if (!rail) return;
    const onWheel = (event: WheelEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      const queue = target?.closest<HTMLElement>(".track-prompt-list") ?? null;
      const body = rail.querySelector<HTMLElement>(".track-rail-body");
      const direction = Math.sign(event.deltaY);
      const queueCanMove = queue && (
        (direction < 0 && queue.scrollTop > 0) ||
        (direction > 0 && queue.scrollTop + queue.clientHeight < queue.scrollHeight)
      );
      const scroller = queueCanMove ? queue : body;
      if (!scroller || event.deltaY === 0) return;
      scroller.scrollTop += event.deltaY;
      event.preventDefault();
      event.stopPropagation();
    };
    rail.addEventListener("wheel", onWheel, { passive: false });
    return () => rail.removeEventListener("wheel", onWheel);
  }, []);

  return (
    <div ref={railRef} className="card track-rail" hidden={hidden}>
      <div className="row spread labels-3d-header">
        <h3 style={{ margin: 0 }}>Track (SAM2)</h3>
        <div className="track-history-actions" aria-label="Track prompt history">
          <button type="button" className="secondary" title={`Undo prompt (${promptUndoCount} available)`} disabled={blocked || reviewLocked || promptUndoCount === 0} onClick={onPromptUndo}>Undo</button>
          <button type="button" className="secondary" title={`Redo prompt (${promptRedoCount} available)`} disabled={blocked || reviewLocked || promptRedoCount === 0} onClick={onPromptRedo}>Redo</button>
        </div>
      </div>
      <fieldset className="track-rail-shell" disabled={blocked}>
        <div className="track-rail-body">
        <p>{axisIsZ ? "Create tracking prompts here. They never change annotation labels until propagation succeeds." : "Switch to axial (z) view to use Track."}</p>

        <section className="track-prompt-tools" aria-label="SAM tracking prompt tools">
          <strong>Child-class prompt tools</strong>
          <div className="track-tool-grid">
            {PROMPT_TOOL_CELLS.map((cell, index) => cell ? (
              <button key={cell.tool} type="button" className={`secondary${promptTool === cell.tool ? " selected" : ""}`} aria-pressed={promptTool === cell.tool} disabled={!canEdit || reviewLocked} title={cell.title} onClick={() => onPromptTool(cell.tool)}>{cell.label}</button>
            ) : (
              <span key={`spacer-${index}`} className="track-tool-spacer" aria-hidden="true" />
            ))}
          </div>
          <div className="track-prompt-edit-controls">
            <button type="button" className="secondary" disabled={!promptEditing || savingProgress} title="Keep committed prompts in the Track queue, pause editing, and resume later by selecting a prompt tool." onClick={onSaveProgress}>{savingProgress ? "Saving…" : "Save progress"}</button>
            {!promptEditing && progressSaved && <span className="muted track-progress-saved" role="status">Progress saved — select a tool to resume.</span>}
            <label className={`track-size-control${promptTool === "brush" || promptTool === "erase" ? "" : " inactive"}`}>
              {promptTool === "erase" ? "Eraser" : "Brush"} size
              <input type="range" min={1} max={64} disabled={promptTool !== "brush" && promptTool !== "erase"} value={promptTool === "erase" ? promptEraserSize : promptBrushSize} onChange={(e) => (promptTool === "erase" ? onPromptEraserSize : onPromptBrushSize)(Number(e.target.value))} />
              <span>{promptTool === "erase" ? promptEraserSize : promptBrushSize}px</span>
            </label>
          </div>
        </section>

        <section className="track-queue-panel" aria-label="Track parent queue">
          <div className="track-queue-heading">
            <strong>Queued parents</strong>
            <span className="muted">{prompts.length} queued · {readyCount} ready</span>
          </div>
          <div className="track-prompt-list" role="listbox" aria-label="Queued parent classes">
            {prompts.map((prompt) => {
              const isSelected = prompt.parent_id === selectedParentId;
              const childLabel = `${prompt.subclasses.length} child${prompt.subclasses.length === 1 ? "" : "ren"}`;
              return (
                <div role="option" aria-selected={isSelected} className={`track-parent-item${isSelected ? " selected" : ""}`} key={prompt.parent_id}>
                  <div className="track-parent-row">
                    <button type="button" className="track-prompt-row" onClick={() => onSelectPrompt(prompt.parent_id)}>
                      <span className="track-parent-caret" aria-hidden="true">{isSelected ? "▾" : "▸"}</span>
                      <span className="track-parent-dot" style={{ background: prompt.parent_id === activeId ? activeColorCss : undefined }} />
                      <strong>Parent {prompt.parent_id}</strong>
                      <span>{childLabel}</span>
                      <small>{prompt.status}</small>
                    </button>
                    {isSelected && (
                      <button type="button" className="secondary track-remove" disabled={reviewLocked} title={`Remove parent class ${prompt.parent_id} from the queue`} aria-label={`Remove parent class ${prompt.parent_id}`} onClick={onRemovePrompt}>×</button>
                    )}
                  </div>
                  {isSelected && (
                    <div className="track-subclass-panel">
                      {prompt.subclasses.map((child) => (
                        <div className={`track-subclass-row${child.index === selectedChildIndex ? " selected" : ""}`} key={child.index}>
                          <button type="button" className="secondary" aria-pressed={child.index === selectedChildIndex} onClick={() => onSelectChild(child.index)}>Child class {child.index}</button>
                          <span className="muted">{child.seeds.map((seed) => `z=${seed.z}`).join(", ") || "draw a seed"}</span>
                          <button type="button" className="secondary track-remove" disabled={reviewLocked} aria-label={`Remove child class ${child.index}`} onClick={() => onRemoveChild(child.index)}>×</button>
                        </div>
                      ))}
                      <div className="track-inline-actions">
                        <button type="button" className="secondary" disabled={reviewLocked} onClick={onAddChild}>+ child</button>
                        <button type="button" className="secondary" onClick={onClearSeed} disabled={reviewLocked || !selectedChild}>Clear this z</button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
            {!prompts.length && <p className="muted track-queue-empty">No parent classes queued. Choose an active class, add it below, then select a child-class prompt tool and draw on the image.</p>}
          </div>
          <button type="button" className="track-add-parent" onClick={onQueueActive} disabled={reviewLocked}>Add parent class {activeId} to queue</button>
          <div className="track-range-row" role="group" aria-label="Selected parent propagation range">
            <span><strong>Start layer</strong> {startLayer ?? "—"}</span>
            <span><strong>End layer</strong> {endLayer ?? "—"}</span>
          </div>
          {tracking && (
            <div className="track-propagation-status" role="status">
              <span>
                {trackingParentIds.length === 1
                  ? `Propagating parent ${trackingParentIds[0]}…`
                  : `Propagating ${trackingParentIds.length || readyCount} parents…`}
                {` running ${trackingElapsed}s`}
              </span>
              <div className="track-progress" role="progressbar" aria-label="Track propagation in progress">
                <div className="track-progress-fill track-progress-indeterminate" />
              </div>
            </div>
          )}
          <label className="track-overwrite-control">
            <span>Overwrite</span>
            <select
              aria-label="Track overwrite"
              value={overwriteMode}
              disabled={reviewLocked || tracking}
              onChange={(event) => onOverwriteMode(event.target.value as OverwriteMode)}
            >
              <option value="overwrite_empty">Empty voxels only</option>
              <option value="overwrite_all">All voxels</option>
            </select>
          </label>
          <div className="track-action-grid track-propagate-actions" aria-label="Track propagation actions">
            <button type="button" className="track-propagate-selected" title="Propagate every child-class seed under the selected parent class" onClick={onPropagateSelected} disabled={reviewLocked || tracking || !selected || !selected.subclasses.some((child) => child.seeds.length)}>{tracking ? "Propagating…" : "Propagate selected"}</button>
            <button type="button" className="secondary track-propagate-all" title="Propagate every queued parent class with all of its child-class seeds" onClick={onPropagateAll} disabled={reviewLocked || tracking || readyCount === 0}>{`Propagate all (${readyCount})`}</button>
          </div>
        </section>

        </div>
      </fieldset>

      {/* Deliberately *outside* the disabled fieldset. A completed propagate
          leaves work that only Confirm or Reject can resolve, so these two
          must stay clickable even while everything that could produce more
          work is blocked — a saving Save, a non-z axis, a busy whole-volume
          tool. Putting them inside the fieldset is what made a finished
          propagate un-reviewable (a disabled fieldset disables every control
          nested under it, not just its direct children). */}
      <div className="track-rail-footer">
        <section className="track-review-panel" aria-label="Pending Track preview review">
          <div className="track-review-actions">
            <button type="button" className="track-review-confirm" title={reviewing ? `Confirm pending Track preview for parents ${pendingParentIds.join(", ")}` : "No pending Track preview to confirm"} disabled={tracking || reviewAction != null || !reviewing} onClick={() => onReview("confirm")}>{reviewAction === "confirm" ? "Confirming…" : "Confirm"}</button>
            <button type="button" className="secondary track-review-reject" title={reviewing ? `Reject pending Track preview for parents ${pendingParentIds.join(", ")}` : "No pending Track preview to reject"} disabled={tracking || reviewAction != null || !reviewing} onClick={() => onReview("reject")}>{reviewAction === "reject" ? "Rejecting…" : "Reject"}</button>
          </div>
        </section>
        {trackError && <span className="error track-rail-error">{trackError}</span>}
      </div>
    </div>
  );
}
