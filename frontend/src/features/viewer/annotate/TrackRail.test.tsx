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
  layerCount = 20,
) {
  const onPromptTool = vi.fn();
  const onSaveProgress = vi.fn();
  const onPromptUndo = vi.fn();
  const onPromptRedo = vi.fn();
  const onRemovePrompt = vi.fn();
  const onClearSeed = vi.fn();
  const onReview = vi.fn();
  const onRange = vi.fn();
  const view = render(<TrackRail
    hidden={false} disabled={false} activeId={50} activeColorCss="#fff"
    tracking={tracking} trackingParentIds={tracking ? [50, 51] : []}
    promptEditing={promptTool != null} promptTool={promptTool} promptBrushSize={8} promptEraserSize={12}
    savingProgress={false} progressSaved={progressSaved}
    axisIsZ prompts={prompts} selectedParentId={prompts[0]?.parent_id ?? null}
    pendingReview={pendingReview} promptUndoCount={promptUndoCount} promptRedoCount={promptRedoCount}
    reviewAction={reviewAction}
    overwriteMode="overwrite_empty"
    layerCount={layerCount} onRange={onRange}
    onSelectPrompt={vi.fn()} onQueueActive={vi.fn()}
    onPromptTool={onPromptTool} onSaveProgress={onSaveProgress} onPromptBrushSize={vi.fn()} onPromptEraserSize={vi.fn()}
    onClearSeed={onClearSeed}
    onRemovePrompt={onRemovePrompt} onPropagateAll={vi.fn()}
    onPropagateSelected={vi.fn()} onPromptUndo={onPromptUndo} onPromptRedo={onPromptRedo}
    onOverwriteMode={vi.fn()}
    onReview={onReview}
  />);
  return { onPromptTool, onSaveProgress, onPromptUndo, onPromptRedo, onRemovePrompt, onClearSeed, onReview, onRange, ...view };
}

describe("TrackRail", () => {
  it("shows an unlimited scrollable class queue with no sub-class rows", () => {
    const prompts = Array.from({ length: 25 }, (_, i): TrackingPrompt => ({
      parent_id: i + 1,
      subclasses: [{ index: 1, seeds: [] }],
      start_z: 0,
      end_z: 9,
      z_range: [0, 9],
      status: "draft",
    }));
    renderRail(prompts);
    expect(screen.getByRole("listbox", { name: "Queued classes" }).querySelectorAll('[role="option"]')).toHaveLength(25);
    expect(screen.getByText("Class 25")).toBeTruthy();
    // Branch splitting is the backend's job, so there is nothing per-class to
    // expand, select or name here.
    expect(screen.queryByRole("button", { name: /Child class/ })).toBeNull();
    expect(screen.queryByText(/Subclass/)).toBeNull();
  });

  it("activates the seed prompt tools", () => {
    const { onPromptTool } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      start_z: 0,
      end_z: 9,
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
      start_z: 0,
      end_z: 9,
      z_range: [0, 9],
      status: "draft",
    }], "brush");
    expect(screen.getByRole("region", { name: "Track seed tools" })).toBeTruthy();
    for (const name of ["Brush", "Erase", "Box erase", "Box", "Point"]) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
    expect(screen.getByRole("button", { name: "Brush" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("lays out prompt tools as Brush|Erase, Box|Box erase, Point|empty", () => {
    renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      start_z: 0,
      end_z: 9,
      z_range: [0, 9],
      status: "draft",
    }]);
    const grid = screen.getByRole("region", { name: "Track seed tools" })
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
      start_z: 3,
      end_z: 3,
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
      start_z: 3,
      end_z: 3,
      z_range: [3, 3],
      status: "ready",
    }], null, null, 0, 0, true);
    expect(screen.getByRole("status").textContent).toContain("Progress saved");
    fireEvent.click(screen.getByRole("button", { name: "Brush" }));
    expect(onPromptTool).toHaveBeenCalledWith("brush");
  });

  it("has no manual child creation and no Track Split control", () => {
    // The backend infers branches from the disconnected pieces of one drawing,
    // so asking the annotator to create and pick sub-classes by hand described
    // a workflow the server no longer has.
    renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      start_z: 0,
      end_z: 0,
      z_range: [0, 0],
      status: "draft",
    }]);
    expect(screen.queryByRole("button", { name: "+ child" })).toBeNull();
    expect(screen.queryByRole("button", { name: /child/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Split/ })).toBeNull();
  });

  it("shows animated propagation status for the entire request", () => {
    const { container } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [{ z: 3, shape: [2, 2], rle: [[0, 1]] }] }],
      start_z: 3,
      end_z: 3,
      z_range: [3, 3],
      status: "running",
    }], null, null, 0, 0, false, true);
    expect(screen.getByText(/Propagating 2 classes… running 0s/)).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "Track propagation in progress" })).toBeTruthy();
    expect(container.querySelector(".track-propagation-status .track-progress-indeterminate")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Propagating…" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Propagate all (1)" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("does not show progress chrome while preparing an AI prompt proposal", () => {
    const { container } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      start_z: 0,
      end_z: 0,
      z_range: [0, 0],
      status: "draft",
    }], "point");
    expect(screen.queryByText("Preparing AI proposal…")).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(container.querySelector(".track-ai-busy")).toBeNull();
  });

  it("does not render instructional footer text below the prompt tools", () => {
    renderRail([]);
    const tools = screen.getByRole("region", { name: "Track seed tools" });
    expect(tools.querySelector("p")).toBeNull();
    expect(screen.queryByText(/Select a parent class and child class/)).toBeNull();
  });

  it("puts compact prompt Undo and Redo controls in the Track header", () => {
    const { container, onPromptUndo, onPromptRedo } = renderRail([], null, null, 2, 1);
    const headerActions = container.querySelector<HTMLElement>(".track-history-actions")!;
    const tools = screen.getByRole("region", { name: "Track seed tools" });
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
      start_z: 0,
      end_z: 9,
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
      start_z: 0,
      end_z: 9,
      z_range: [0, 9],
      status: "ready",
    }]);
    const actions = container.querySelector<HTMLElement>(".track-propagate-actions")!;
    expect(actions.querySelectorAll("button")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Propagate selected" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Propagate all (1)" })).toBeTruthy();
    // When it is enabled the tooltip describes the action; when it is blocked
    // it says exactly what is missing instead (see the gating test below).
    expect(screen.getByRole("button", { name: "Propagate selected" }).getAttribute("title"))
      .toContain("across its Start–End range");
    expect(screen.queryByText(/One-click/)).toBeNull();
    expect(screen.queryByText(/Paint directly on the image/)).toBeNull();
  });

  it("places the Queue heading and its list above Add class, then Start/End", () => {
    const { container } = renderRail([]);
    const heading = container.querySelector(".track-queue-heading")!;
    const list = screen.getByRole("listbox", { name: "Queued classes" });
    const add = screen.getByRole("button", { name: "Add class 50 to queue" });
    const range = screen.getByRole("group", { name: "Selected class propagation range" });
    expect(heading.textContent).toContain("Queue");
    expect(heading.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(list.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(add.compareDocumentPosition(range) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(add.closest(".track-queue-panel")).toBe(range.closest(".track-queue-panel"));
    expect(range.querySelector("button")).toBeNull();
  });

  it("labels Add with the fresh queue class id, not only the current Active", () => {
    render(<TrackRail
      hidden={false} disabled={false} activeId={50} queueClassId={61} activeColorCss="#fff"
      tracking={false} trackingParentIds={[]} promptEditing={false} promptTool={null}
      savingProgress={false} progressSaved={false}
      promptBrushSize={8} promptEraserSize={12} axisIsZ prompts={[]}
      selectedParentId={null} pendingReview={null}
      reviewAction={null} promptUndoCount={0} promptRedoCount={0}
      overwriteMode="overwrite_empty" layerCount={20} onRange={vi.fn()}
      onSelectPrompt={vi.fn()} onQueueActive={vi.fn()} onPromptTool={vi.fn()}
      onSaveProgress={vi.fn()} onPromptBrushSize={vi.fn()} onPromptEraserSize={vi.fn()}
      onClearSeed={vi.fn()} onRemovePrompt={vi.fn()}
      onPromptUndo={vi.fn()} onPromptRedo={vi.fn()} onOverwriteMode={vi.fn()}
      onPropagateAll={vi.fn()} onPropagateSelected={vi.fn()} onReview={vi.fn()}
    />);
    expect(screen.getByRole("button", { name: "Add class 61 to queue" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add class 50 to queue" })).toBeNull();
  });

  it("shows Track overwrite immediately above propagation and reports changes", () => {
    const onOverwriteMode = vi.fn();
    const { container } = render(<TrackRail
      hidden={false} disabled={false} activeId={50} activeColorCss="#fff"
      tracking={false} trackingParentIds={[]} promptEditing={false} promptTool={null}
      savingProgress={false} progressSaved={false}
      promptBrushSize={8} promptEraserSize={12} axisIsZ prompts={[]}
      selectedParentId={null} pendingReview={null}
      reviewAction={null} promptUndoCount={0} promptRedoCount={0}
      overwriteMode="overwrite_empty" layerCount={20} onRange={vi.fn()}
      onSelectPrompt={vi.fn()} onQueueActive={vi.fn()} onPromptTool={vi.fn()}
      onSaveProgress={vi.fn()} onPromptBrushSize={vi.fn()} onPromptEraserSize={vi.fn()}
      onClearSeed={vi.fn()} onRemovePrompt={vi.fn()}
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

  it("shows the explicit Start/End as editable 1-based layer fields", () => {
    // The API stores 0-based z; the viewer shows 1-based layers everywhere
    // else, so 0..11 must read as layers 1 and 12 — not 0 and 11.
    renderRail([{
      parent_id: 50,
      subclasses: [
        { index: 1, seeds: [{ z: 8, shape: [2, 2], rle: [[0, 1]] }] },
        { index: 2, seeds: [{ z: 3, shape: [2, 2], rle: [[1, 1]] }] },
      ],
      start_z: 0,
      end_z: 11,
      z_range: [0, 11],
      status: "ready",
    }]);
    const start = screen.getByRole("textbox", { name: "Start layer" }) as HTMLInputElement;
    const end = screen.getByRole("textbox", { name: "End layer" }) as HTMLInputElement;
    expect(start.value).toBe("1");
    expect(end.value).toBe("12");
    // The seed layers (3 and 8) are deliberately not what the fields show.
    expect(start.value).not.toBe("4");
    expect(end.value).not.toBe("9");
  });

  it("shows a placeholder range when no class is selected", () => {
    // An editable "1" here would read as a range the annotator had chosen.
    renderRail([]);
    const range = screen.getByRole("group", { name: "Selected class propagation range" });
    expect(range.textContent).toContain("Start layer");
    expect(range.textContent).toContain("—");
    expect(screen.queryByRole("textbox", { name: "Start layer" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: "End layer" })).toBeNull();
  });

  it("commits a typed layer number back as a 0-based z", () => {
    const { onRange } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [{ z: 3, shape: [2, 2], rle: [[0, 1]] }] }],
      start_z: 3,
      end_z: 3,
      z_range: [3, 3],
      status: "ready",
    }]);
    const end = screen.getByRole("textbox", { name: "End layer" });
    fireEvent.change(end, { target: { value: "10" } });
    fireEvent.blur(end);
    expect(onRange).toHaveBeenCalledWith(50, 3, 9);
  });

  it("clamps a typed layer to the volume depth", () => {
    const { onRange } = renderRail(
      [{
        parent_id: 50,
        subclasses: [{ index: 1, seeds: [{ z: 3, shape: [2, 2], rle: [[0, 1]] }] }],
        start_z: 3,
        end_z: 3,
        z_range: [3, 3],
        status: "ready",
      }],
      null, null, 0, 0, false, false, null,
      6, // a six-layer volume: layer 99 cannot exist
    );
    const end = screen.getByRole("textbox", { name: "End layer" });
    fireEvent.change(end, { target: { value: "99" } });
    fireEvent.blur(end);
    expect(onRange).toHaveBeenCalledWith(50, 3, 5);
  });

  it("blocks Propagate until the range and prompts are valid", () => {
    const base = {
      parent_id: 50,
      subclasses: [{
        index: 1,
        seeds: [{ z: 3, shape: [2, 2] as [number, number], rle: [[0, 1]] as [number, number][] }],
      }],
      status: "ready" as const,
    };
    const propagate = () =>
      screen.getByRole("button", { name: "Propagate selected" }) as HTMLButtonElement;
    const all = () => screen.getByRole("button", { name: /^Propagate all/ }) as HTMLButtonElement;

    // No range chosen at all.
    const missing = renderRail([{ ...base, start_z: null, end_z: null, z_range: [0, 0] }]);
    expect(propagate().disabled).toBe(true);
    expect(all().textContent).toBe("Propagate all (0)");
    expect(propagate().title).toMatch(/Set both Start and End/);
    missing.unmount();

    // Reversed range.
    const reversed = renderRail([{ ...base, start_z: 8, end_z: 2, z_range: [8, 2] }]);
    expect(propagate().disabled).toBe(true);
    expect(propagate().title).toMatch(/must not be before Start layer/);
    reversed.unmount();

    // A seed outside the chosen range, reported in layer numbers.
    const outside = renderRail([{ ...base, start_z: 5, end_z: 8, z_range: [5, 8] }]);
    expect(propagate().disabled).toBe(true);
    expect(propagate().title).toMatch(/Seed layer 4 falls? outside 6–9/);
    outside.unmount();

    // Seeds but no range vs. a range but no seeds — both blocked.
    const seedless = renderRail([{
      ...base, subclasses: [{ index: 1, seeds: [] }], start_z: 0, end_z: 9, z_range: [0, 9],
    }]);
    expect(propagate().disabled).toBe(true);
    expect(propagate().title).toMatch(/Draw at least one seed/);
    seedless.unmount();

    // Valid: one seed inside a wider inclusive range.
    renderRail([{ ...base, start_z: 0, end_z: 9, z_range: [0, 9] }]);
    expect(propagate().disabled).toBe(false);
    expect(all().disabled).toBe(false);
    expect(all().textContent).toBe("Propagate all (1)");
  });

  it("does not render a post-propagate genealogy report", () => {
    renderRail(
      [{
        parent_id: 50,
        subclasses: [{ index: 1, seeds: [{ z: 0, shape: [2, 2], rle: [[0, 1]] }] }],
        start_z: 0, end_z: 5, z_range: [0, 5], status: "pending",
      }],
      null,
      { parent_ids: [50], status: "pending_review" },
    );
    expect(screen.queryByRole("region", { name: "Track propagation summary" })).toBeNull();
    expect(screen.queryByText(/Branch .*merged/)).toBeNull();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reject" })).toBeTruthy();
  });

  it("offers Confirm/Reject for a pending class preview and blocks propagation", () => {
    renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [{ z: 4, shape: [2, 2], rle: [[0, 1]] }] }],
      start_z: 4,
      end_z: 4,
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

  it("keeps multi-class review ids in button titles without visible copy", () => {
    renderRail([], null, { parent_ids: [50, 51, 52], status: "pending_review" });
    const confirm = screen.getByRole("button", { name: "Confirm" }) as HTMLButtonElement;
    expect(confirm.title).toContain("50, 51, 52");
    expect(confirm.disabled).toBe(false);
    expect((screen.getByRole("button", { name: "Reject" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByText(/Review propagated class/)).toBeNull();
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
      start_z: 4,
      end_z: 4,
      z_range: [4, 4],
      status: "pending",
    }]);
    expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reject" })).toBeTruthy();
  });

  it("attaches compact propagation controls to the queue panel", () => {
    const { container } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      start_z: 0,
      end_z: 9,
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
  it("expands the selected class inline to its seed actions", () => {
    const { container, onClearSeed } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      start_z: 0,
      end_z: 9,
      z_range: [0, 9],
      status: "draft",
    }]);
    const list = container.querySelector(".track-prompt-list")!;
    const selectedClass = list.querySelector(".track-class-item.selected")!;
    const detail = selectedClass.querySelector(".track-inline-actions")!;
    expect(selectedClass.contains(detail)).toBe(true);
    expect(selectedClass.getAttribute("aria-selected")).toBe("true");
    expect(selectedClass.querySelector(".track-subclass-panel")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Clear this z" }));
    expect(onClearSeed).toHaveBeenCalledOnce();
  });

  it("never pins the prompt tools over the content below them", () => {
    // Regression: `.track-prompt-tools { position: sticky; top: 0 }` floated
    // the pink panel over the queue list below it.
    const { container } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      start_z: 0,
      end_z: 9,
      z_range: [0, 9],
      status: "draft",
    }], "brush");
    const tools = container.querySelector<HTMLElement>(".track-prompt-tools")!;
    expect(tools.style.position).not.toBe("sticky");
    // It must remain in normal flow before the queue it used to overlap.
    const panel = container.querySelector(".track-queue-panel")!;
    expect(tools.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(tools.closest(".track-rail-body")).toBe(panel.closest(".track-rail-body"));
  });

  it("removes a class with a small × at the end of its row", () => {
    const { container, onRemovePrompt } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      start_z: 0,
      end_z: 9,
      z_range: [0, 9],
      status: "draft",
    }]);
    expect(screen.queryByRole("button", { name: "Remove parent" })).toBeNull();
    const remove = screen.getByRole("button", { name: "Remove class 50" });
    expect(remove.textContent).toBe("×");
    expect(remove.classList.contains("danger")).toBe(false);
    expect(remove.classList.contains("track-remove")).toBe(true);
    const row = container.querySelector<HTMLElement>(".track-class-item.selected .track-class-row")!;
    expect(row.lastElementChild).toBe(remove);
    fireEvent.click(remove);
    expect(onRemovePrompt).toHaveBeenCalledOnce();
  });

  it("reports how many layers a class is seeded on instead of listing slots", () => {
    const { container } = renderRail([{
      parent_id: 50,
      // Two slots seeded on three distinct layers. The annotator is told about
      // the layers, which is what they can act on; the slots are the backend's
      // branch bookkeeping and stay out of the queue row.
      subclasses: [
        { index: 1, seeds: [{ z: 7, shape: [2, 2], rle: [[0, 1]] }, { z: 8, shape: [2, 2], rle: [[0, 1]] }] },
        { index: 2, seeds: [{ z: 8, shape: [2, 2], rle: [[0, 1]] }, { z: 9, shape: [2, 2], rle: [[0, 1]] }] },
      ],
      start_z: 7,
      end_z: 9,
      z_range: [7, 9],
      status: "ready",
    }]);
    const row = container.querySelector<HTMLElement>(".track-class-item.selected")!;
    expect(row.textContent).toContain("3 seed layers");
    expect(row.textContent).not.toContain("z=7");
    expect(screen.queryByRole("button", { name: /Child class/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove child/ })).toBeNull();
    expect(container.querySelector(".track-subclass-row")).toBeNull();
  });

  it("says a class has no seeds rather than showing an empty slot list", () => {
    const { container } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [] }],
      start_z: 0,
      end_z: 9,
      z_range: [0, 9],
      status: "draft",
    }]);
    expect(container.querySelector(".track-class-item.selected")!.textContent).toContain("no seeds");
  });

  it("keeps no parent or child vocabulary anywhere the annotator can read it", () => {
    const { container } = renderRail([{
      parent_id: 50,
      subclasses: [{ index: 1, seeds: [{ z: 7, shape: [2, 2], rle: [[0, 1]] }] }],
      start_z: 7,
      end_z: 7,
      z_range: [7, 7],
      status: "draft",
    }], "brush");
    expect(container.textContent).not.toMatch(/parent|child/i);
    for (const element of Array.from(container.querySelectorAll("[title], [aria-label]"))) {
      expect(element.getAttribute("title") ?? "").not.toMatch(/parent|child/i);
      expect(element.getAttribute("aria-label") ?? "").not.toMatch(/parent|child/i);
    }
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
      axisIsZ={false} prompts={[]} selectedParentId={null}
      pendingReview={{ parent_ids: [50], status: "pending_review" }}
      promptUndoCount={0} promptRedoCount={0} reviewAction={null}
      overwriteMode="overwrite_empty"
      layerCount={20} onRange={vi.fn()}
      onSelectPrompt={vi.fn()} onQueueActive={vi.fn()}
      onPromptTool={vi.fn()} onSaveProgress={vi.fn()}
      onPromptBrushSize={vi.fn()} onPromptEraserSize={vi.fn()} onClearSeed={vi.fn()}
      onRemovePrompt={vi.fn()} onPropagateAll={vi.fn()}
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
