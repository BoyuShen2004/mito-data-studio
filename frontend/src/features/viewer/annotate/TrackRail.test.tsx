import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { TrackingPrompt } from "../../../api/viewer";
import TrackRail from "./TrackRail";

function renderRail(
  prompts: TrackingPrompt[],
  promptTool: "brush" | "erase" | "box_erase" | "box" | "point" | null = null,
  pendingReview: { parent_ids: number[]; status: "pending_review" } | null = null,
  promptUndoCount = 0,
  promptRedoCount = 0,
  progressSaved = false,
  tracking = false,
  reviewAction: "confirm" | "reject" | null = null,
) {
  const onPromptTool = vi.fn();
  const onSaveProgress = vi.fn();
  const onPromptUndo = vi.fn();
  const onPromptRedo = vi.fn();
  const onRemovePrompt = vi.fn();
  const onRemoveChild = vi.fn();
  const onReview = vi.fn();
  const view = render(<TrackRail
    hidden={false} disabled={false} activeId={50} activeColorCss="#fff"
    tracking={tracking} trackingParentIds={tracking ? [50, 51] : []}
    promptEditing={promptTool != null} promptTool={promptTool} promptBrushSize={8} promptEraserSize={12}
    savingProgress={false} progressSaved={progressSaved}
    trackError={null} axisIsZ prompts={prompts} selectedParentId={prompts[0]?.parent_id ?? null}
    selectedChildIndex={prompts[0]?.subclasses[0]?.index ?? null}
    pendingReview={pendingReview} promptUndoCount={promptUndoCount} promptRedoCount={promptRedoCount}
    reviewAction={reviewAction}
    overwriteMode="overwrite_empty"
    onSelectPrompt={vi.fn()}
    onSelectChild={vi.fn()} onQueueActive={vi.fn()} onAddChild={vi.fn()}
    onPromptTool={onPromptTool} onSaveProgress={onSaveProgress} onPromptBrushSize={vi.fn()} onPromptEraserSize={vi.fn()}
    onClearSeed={vi.fn()} onRemoveChild={onRemoveChild}
    onRemovePrompt={onRemovePrompt} onPropagateAll={vi.fn()}
    onPropagateSelected={vi.fn()} onPromptUndo={onPromptUndo} onPromptRedo={onPromptRedo}
    onOverwriteMode={vi.fn()}
    onReview={onReview}
  />);
  return { onPromptTool, onSaveProgress, onPromptUndo, onPromptRedo, onRemovePrompt, onRemoveChild, onReview, ...view };
}

describe("TrackRail", () => {
  it("shows an unlimited scrollable parent-class queue with local child classes", () => {
    const prompts = Array.from({ length: 25 }, (_, i): TrackingPrompt => ({
      parent_id: i + 1,
      subclasses: [{ index: 1, seeds: [] }],
      z_range: [0, 9],
      status: "draft",
    }));
    renderRail(prompts);
    expect(screen.getByRole("listbox", { name: "Queued parent classes" }).querySelectorAll('[role="option"]')).toHaveLength(25);
    expect(screen.getByText("Parent 25")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Child class 1" })).toBeTruthy();
    expect(screen.queryByText(/Subclass/)).toBeNull();
  });

  it("activates dedicated child-class prompt tools", () => {
    const { onPromptTool } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      z_range: [0, 9],
      status: "draft",
    }]);
    fireEvent.click(screen.getByRole("button", { name: "Brush" }));
    expect(onPromptTool).toHaveBeenCalledWith("brush");
    for (const name of ["Erase", "Box erase", "Box", "Point"]) {
      expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(false);
    }
  });

  it("keeps the complete prompt tool section mounted while a tool is active", () => {
    renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      z_range: [0, 9],
      status: "draft",
    }], "brush");
    expect(screen.getByRole("region", { name: "SAM tracking prompt tools" })).toBeTruthy();
    for (const name of ["Brush", "Erase", "Box erase", "Box", "Point"]) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
    expect(screen.getByRole("button", { name: "Brush" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("lays out prompt tools as Brush|Erase, Box|Box erase, Point|empty", () => {
    renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      z_range: [0, 9],
      status: "draft",
    }]);
    const grid = screen.getByRole("region", { name: "SAM tracking prompt tools" })
      .querySelector(".track-tool-grid")!;
    expect(Array.from(grid.children).map((element) =>
      element.classList.contains("track-tool-spacer") ? "empty" : element.textContent,
    )).toEqual(["Brush", "Erase", "Box", "Box erase", "Point", "empty"]);
    expect(grid.querySelector(".track-tool-spacer")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("saves prompt progress through the dedicated pause action", () => {
    const { onPromptTool, onSaveProgress } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [{ z: 3, shape: [2, 2], rle: [[0, 1]] }] }],
      z_range: [3, 3],
      status: "ready",
    }], "box");
    const save = screen.getByRole("button", { name: "Save progress" });
    expect(save.getAttribute("title")).toContain("resume later");
    fireEvent.click(save);
    expect(onSaveProgress).toHaveBeenCalledOnce();
    expect(onPromptTool).not.toHaveBeenCalledWith(null);
  });

  it("shows the saved hint while idle and resumes through any prompt tool", () => {
    const { onPromptTool } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [{ z: 3, shape: [2, 2], rle: [[0, 1]] }] }],
      z_range: [3, 3],
      status: "ready",
    }], null, null, 0, 0, true);
    expect(screen.getByRole("status").textContent).toContain("Progress saved");
    fireEvent.click(screen.getByRole("button", { name: "Brush" }));
    expect(onPromptTool).toHaveBeenCalledWith("brush");
  });

  it("keeps manual child creation and has no Track Split control", () => {
    renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      z_range: [0, 0],
      status: "draft",
    }]);
    expect(screen.getByRole("button", { name: "+ child" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Split/ })).toBeNull();
  });

  it("shows animated propagation status for the entire request", () => {
    const { container } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [{ z: 3, shape: [2, 2], rle: [[0, 1]] }] }],
      z_range: [3, 3],
      status: "running",
    }], null, null, 0, 0, false, true);
    expect(screen.getByText(/Propagating 2 parents… running 0s/)).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "Track propagation in progress" })).toBeTruthy();
    expect(container.querySelector(".track-propagation-status .track-progress-indeterminate")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Propagating…" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Propagate all (1)" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("does not show progress chrome while preparing an AI prompt proposal", () => {
    const { container } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      z_range: [0, 0],
      status: "draft",
    }], "point");
    expect(screen.queryByText("Preparing AI proposal…")).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(container.querySelector(".track-ai-busy")).toBeNull();
  });

  it("does not render instructional footer text below the prompt tools", () => {
    renderRail([]);
    const tools = screen.getByRole("region", { name: "SAM tracking prompt tools" });
    expect(tools.querySelector("p")).toBeNull();
    expect(screen.queryByText(/Select a parent class and child class/)).toBeNull();
  });

  it("puts compact prompt Undo and Redo controls in the Track header", () => {
    const { container, onPromptUndo, onPromptRedo } = renderRail([], null, null, 2, 1);
    const headerActions = container.querySelector<HTMLElement>(".track-history-actions")!;
    const tools = screen.getByRole("region", { name: "SAM tracking prompt tools" });
    const undo = screen.getByRole("button", { name: "Undo" });
    const redo = screen.getByRole("button", { name: "Redo" });

    expect(headerActions.contains(undo)).toBe(true);
    expect(headerActions.contains(redo)).toBe(true);
    expect(tools.contains(undo)).toBe(false);
    expect(tools.contains(redo)).toBe(false);
    expect(undo.classList.contains("secondary")).toBe(true);
    expect(redo.classList.contains("secondary")).toBe(true);
    expect(undo.getAttribute("title")).toBe("Undo prompt (2 available)");
    expect(redo.getAttribute("title")).toBe("Redo prompt (1 available)");

    fireEvent.click(undo);
    fireEvent.click(redo);
    expect(onPromptUndo).toHaveBeenCalledOnce();
    expect(onPromptRedo).toHaveBeenCalledOnce();
  });

  it("disables header prompt history controls when their counts are zero", () => {
    renderRail([]);
    expect((screen.getByRole("button", { name: "Undo" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Redo" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /Undo prompt/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Redo prompt/ })).toBeNull();
  });

  it("keeps Box readable by retaining the secondary style in its selected state", () => {
    renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      z_range: [0, 9],
      status: "draft",
    }], "box");
    const box = screen.getByRole("button", { name: "Box" });
    expect(box.classList.contains("secondary")).toBe(true);
    expect(box.classList.contains("selected")).toBe(true);
    expect(box.textContent).toBe("Box");
  });

  it("keeps exactly the selected and all propagation actions", () => {
    const { container } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [{ z: 0, shape: [2, 2], rle: [[0, 1]] }] }],
      z_range: [0, 9],
      status: "ready",
    }]);
    const actions = container.querySelector<HTMLElement>(".track-propagate-actions")!;
    expect(actions.querySelectorAll("button")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Propagate selected" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Propagate all (1)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Propagate selected" }).getAttribute("title")).toContain("every child-class seed");
    expect(screen.queryByText(/One-click/)).toBeNull();
    expect(screen.queryByText(/Paint directly on the image/)).toBeNull();
  });

  it("places Queued parents and its list above Add parent, then Start/End", () => {
    const { container } = renderRail([]);
    const heading = container.querySelector(".track-queue-heading")!;
    const list = screen.getByRole("listbox", { name: "Queued parent classes" });
    const add = screen.getByRole("button", { name: "Add parent class 50 to queue" });
    const range = screen.getByRole("group", { name: "Selected parent propagation range" });
    expect(heading.textContent).toContain("Queued parents");
    expect(heading.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(list.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(add.compareDocumentPosition(range) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(add.closest(".track-queue-panel")).toBe(range.closest(".track-queue-panel"));
    expect(range.querySelector("button")).toBeNull();
  });

  it("shows Track overwrite immediately above propagation and reports changes", () => {
    const onOverwriteMode = vi.fn();
    const { container } = render(<TrackRail
      hidden={false} disabled={false} activeId={50} activeColorCss="#fff"
      tracking={false} trackingParentIds={[]} promptEditing={false} promptTool={null}
      savingProgress={false} progressSaved={false}
      promptBrushSize={8} promptEraserSize={12} trackError={null} axisIsZ prompts={[]}
      selectedParentId={null} selectedChildIndex={null} pendingReview={null}
      reviewAction={null} promptUndoCount={0} promptRedoCount={0}
      overwriteMode="overwrite_empty" onSelectPrompt={vi.fn()} onSelectChild={vi.fn()}
      onQueueActive={vi.fn()} onAddChild={vi.fn()} onPromptTool={vi.fn()}
      onSaveProgress={vi.fn()} onPromptBrushSize={vi.fn()} onPromptEraserSize={vi.fn()}
      onClearSeed={vi.fn()} onRemoveChild={vi.fn()} onRemovePrompt={vi.fn()}
      onPromptUndo={vi.fn()} onPromptRedo={vi.fn()} onOverwriteMode={onOverwriteMode}
      onPropagateAll={vi.fn()} onPropagateSelected={vi.fn()} onReview={vi.fn()}
    />);
    const overwrite = screen.getByRole("combobox", { name: "Track overwrite" });
    const propagation = container.querySelector(".track-propagate-actions")!;
    expect((overwrite as HTMLSelectElement).value).toBe("overwrite_empty");
    expect(overwrite.compareDocumentPosition(propagation) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    fireEvent.change(overwrite, { target: { value: "overwrite_all" } });
    expect(onOverwriteMode).toHaveBeenCalledWith("overwrite_all");
  });

  it("derives Start and End layer from all committed child seeds", () => {
    renderRail([{
      parent_id: 50,
      subclasses: [
        { index: 1, seeds: [{ z: 8, shape: [2, 2], rle: [[0, 1]] }] },
        { index: 2, seeds: [{ z: 3, shape: [2, 2], rle: [[1, 1]] }, { z: 12, shape: [2, 2], rle: [[2, 1]] }] },
      ],
      z_range: [0, 99],
      status: "ready",
    }]);
    const range = screen.getByRole("group", { name: "Selected parent propagation range" });
    expect(range.textContent).toContain("Start layer 3");
    expect(range.textContent).toContain("End layer 12");
  });

  it("offers Confirm/Reject for a pending parent preview and blocks propagation", () => {
    renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [{ z: 4, shape: [2, 2], rle: [[0, 1]] }] }],
      z_range: [4, 4],
      status: "pending",
    }], null, { parent_ids: [50], status: "pending_review" });
    expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reject" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Propagate selected" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("always mounts an idle review box with disabled actions", () => {
    const { container } = renderRail([]);
    const review = screen.getByRole("region", { name: "Pending Track preview review" });
    expect(review.querySelectorAll("button")).toHaveLength(2);
    expect(review.textContent).toBe("ConfirmReject");
    expect(container.querySelector(".track-review-panel.idle")).toBeNull();
    expect(screen.queryByText("No pending Track preview.")).toBeNull();
    expect((screen.getByRole("button", { name: "Confirm" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Reject" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps multi-parent review ids in button titles without visible copy", () => {
    renderRail([], null, { parent_ids: [50, 51, 52], status: "pending_review" });
    const confirm = screen.getByRole("button", { name: "Confirm" }) as HTMLButtonElement;
    expect(confirm.title).toContain("50, 51, 52");
    expect(confirm.disabled).toBe(false);
    expect((screen.getByRole("button", { name: "Reject" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByText(/Review propagated parent/)).toBeNull();
    expect(screen.queryByText(/Scrub z to review/)).toBeNull();
  });

  it("uses review-only busy buttons without propagation chrome or elapsed time", () => {
    const { container } = renderRail([], null, {
      parent_ids: [50], status: "pending_review",
    }, 0, 0, false, false, "confirm");
    expect(screen.getByRole("button", { name: "Confirming…" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Reject" }) as HTMLButtonElement).disabled).toBe(true);
    expect(container.querySelector(".track-propagation-status")).toBeNull();
    expect(screen.queryByRole("progressbar", { name: "Track propagation in progress" })).toBeNull();
    expect(screen.queryByText(/running \d+s/)).toBeNull();
  });

  it("derives Confirm/Reject from pending prompt status when pending_review metadata is missing", () => {
    renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [{ z: 4, shape: [2, 2], rle: [[0, 1]] }] }],
      z_range: [4, 4],
      status: "pending",
    }]);
    expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reject" })).toBeTruthy();
  });

  it("attaches compact propagation controls to the queued-parent panel", () => {
    const { container } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      z_range: [0, 9],
      status: "draft",
    }]);
    const queue = container.querySelector(".track-queue-panel")!;
    const footer = container.querySelector(".track-rail-footer")!;
    const propagation = queue.querySelector(".track-propagate-actions")!;
    expect(queue.contains(propagation)).toBe(true);
    expect(footer.contains(propagation)).toBe(false);
    expect(queue.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  });
  it("expands selected-parent controls inline in the queue", () => {
    const { container } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      z_range: [0, 9],
      status: "draft",
    }]);
    const list = container.querySelector(".track-prompt-list")!;
    const selectedParent = list.querySelector(".track-parent-item.selected")!;
    const detail = selectedParent.querySelector(".track-subclass-panel")!;
    expect(selectedParent.contains(detail)).toBe(true);
    expect(selectedParent.getAttribute("aria-selected")).toBe("true");
    expect(detail.querySelector(".track-subclass-row.selected")).toBeTruthy();
  });

  it("never pins the prompt tools over the content below them", () => {
    // Regression: `.track-prompt-tools { position: sticky; top: 0 }` floated
    // the pink panel over the parent/child panel and the queued-parent list.
    const { container } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      z_range: [0, 9],
      status: "draft",
    }], "brush");
    const tools = container.querySelector<HTMLElement>(".track-prompt-tools")!;
    expect(tools.style.position).not.toBe("sticky");
    // It must remain in normal flow before the queue it used to overlap.
    const panel = container.querySelector(".track-subclass-panel")!;
    expect(tools.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(tools.closest(".track-rail-body")).toBe(panel.closest(".track-rail-body"));
  });

  it("removes a parent with a small × at the end of the parent row", () => {
    const { container, onRemovePrompt } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      z_range: [0, 9],
      status: "draft",
    }]);
    expect(screen.queryByRole("button", { name: "Remove parent" })).toBeNull();
    const remove = screen.getByRole("button", { name: "Remove parent class 50" });
    expect(remove.textContent).toBe("×");
    expect(remove.classList.contains("danger")).toBe(false);
    // Same control shape as the child-class remove, and last in its row.
    expect(remove.classList.contains("track-remove")).toBe(true);
    const row = container.querySelector<HTMLElement>(".track-parent-item.selected .track-parent-row")!;
    expect(row.lastElementChild).toBe(remove);
    fireEvent.click(remove);
    expect(onRemovePrompt).toHaveBeenCalledOnce();
  });

  it("drops the child-class Jump button but keeps select, seed z and ×", () => {
    const { container, onRemoveChild } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [{ z: 7, shape: [2, 2], rle: [[0, 1]] }] }],
      z_range: [7, 7],
      status: "ready",
    }]);
    expect(screen.queryByRole("button", { name: "Jump" })).toBeNull();
    const row = container.querySelector<HTMLElement>(".track-subclass-row")!;
    expect(screen.getByRole("button", { name: "Child class 1" })).toBeTruthy();
    expect(row.textContent).toContain("z=7");
    const remove = screen.getByRole("button", { name: "Remove child class 1" });
    expect(remove.textContent).toBe("×");
    expect(row.lastElementChild).toBe(remove);
    fireEvent.click(remove);
    expect(onRemoveChild).toHaveBeenCalledWith(1);
  });

  it("keeps Confirm/Reject clickable while the rest of the rail is blocked", () => {
    // Regression: the review actions lived inside `<fieldset disabled>`, so a
    // finished propagate could not be resolved whenever anything disabled the
    // rail (a running Save, a non-axial view, a busy whole-volume tool).
    const onReview = vi.fn();
    const { container } = render(<TrackRail
      hidden={false} disabled activeId={50} activeColorCss="#fff"
      tracking={false} trackingParentIds={[]}
      promptEditing={false} promptTool={null} promptBrushSize={8} promptEraserSize={12}
      savingProgress={false} progressSaved={false}
      trackError={null} axisIsZ={false} prompts={[]} selectedParentId={null}
      selectedChildIndex={null}
      pendingReview={{ parent_ids: [50], status: "pending_review" }}
      promptUndoCount={0} promptRedoCount={0} reviewAction={null}
      overwriteMode="overwrite_empty"
      onSelectPrompt={vi.fn()} onSelectChild={vi.fn()} onQueueActive={vi.fn()}
      onAddChild={vi.fn()} onPromptTool={vi.fn()} onSaveProgress={vi.fn()}
      onPromptBrushSize={vi.fn()} onPromptEraserSize={vi.fn()} onClearSeed={vi.fn()}
      onRemoveChild={vi.fn()} onRemovePrompt={vi.fn()} onPropagateAll={vi.fn()}
      onPropagateSelected={vi.fn()} onPromptUndo={vi.fn()} onPromptRedo={vi.fn()}
      onOverwriteMode={vi.fn()}
      onReview={onReview}
    />);
    const confirm = screen.getByRole("button", { name: "Confirm" }) as HTMLButtonElement;
    const reject = screen.getByRole("button", { name: "Reject" }) as HTMLButtonElement;
    expect(confirm.closest("fieldset")).toBeNull();
    expect(reject.closest("fieldset")).toBeNull();
    expect(confirm.disabled).toBe(false);
    expect(reject.disabled).toBe(false);
    // Still the last thing in the rail, below the (disabled) body.
    const shell = container.querySelector(".track-rail-shell")!;
    const footer = container.querySelector(".track-rail-footer")!;
    expect(shell.contains(footer)).toBe(false);
    expect(shell.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    fireEvent.click(confirm);
    expect(onReview).toHaveBeenCalledWith("confirm");
  });

  it("consumes wheel input and scrolls the rail body", () => {
    const { container } = renderRail([]);
    const body = container.querySelector<HTMLElement>(".track-rail-body")!;
    Object.defineProperty(body, "clientHeight", { configurable: true, value: 100 });
    Object.defineProperty(body, "scrollHeight", { configurable: true, value: 500 });
    fireEvent.wheel(body, { deltaY: 60 });
    expect(body.scrollTop).toBe(60);
  });
});
