import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
// Vite's `?raw` — jsdom computes no geometry, so the layout rules that keep
// this row on one line are asserted against the stylesheet itself.
import css from "../../../styles.css?raw";

import AnnotateToolChrome from "./AnnotateToolChrome";
import type { PaintTool } from "./paintTools";

function lastInterpolateButton(): HTMLButtonElement {
  const buttons = screen.getAllByRole("button", { name: "Interpolate" });
  return buttons[buttons.length - 1] as HTMLButtonElement;
}

/** Everything the chrome needs, with the interpolation surface overridable —
 * the rest is inert scaffolding for these tests. */
function renderChrome(
  overrides: Partial<React.ComponentProps<typeof AnnotateToolChrome>> = {},
) {
  const handlers = {
    onPaintTool: vi.fn(),
    onInterpFirst: vi.fn(),
    onInterpLast: vi.fn(),
    onInterpRun: vi.fn(),
  };
  const props: React.ComponentProps<typeof AnnotateToolChrome> = {
    disabled: false,
    paintTool: "select" as PaintTool,
    dirty: false,
    status: "idle",
    sliceLoading: false,
    undoCount: 0,
    redoCount: 0,
    onSave: vi.fn(),
    onShare: vi.fn(),
    canShare: false,
    sharing: false,
    onUndo: vi.fn(),
    onRedo: vi.fn(),
    onDeleteSlice: vi.fn(),
    onResetLabels: vi.fn(),
    resetRunning: false,
    brushSize: 6,
    onBrushSize: vi.fn(),
    eraserSize: 6,
    onEraserSize: vi.fn(),
    cursorStyle: "disc" as const,
    onCursorStyle: vi.fn(),
    activeId: 3,
    onActiveId: vi.fn(),
    onNewInstance: vi.fn(),
    aiError: null,
    aiPointCount: 0,
    hasAiPreview: false,
    onFinalizeAiPoints: vi.fn(),
    onCommitAiPreview: vi.fn(),
    onClearAiPoints: vi.fn(),
    wsTargetLabel: null,
    wsSeedCount: 0,
    wsRunning: false,
    onClearWsSeeds: vi.fn(),
    onRunWatershed: vi.fn(),
    interpolationEnabled: true,
    floodFillEnabled: true,
    overwriteMode: "overwrite_empty",
    onOverwriteMode: vi.fn(),
    floodDepth: 1,
    onFloodDepth: vi.fn(),
    floodRunning: false,
    interpFirst: null,
    interpLast: null,
    interpRunning: false,
    axisLabel: "z",
    currentIndex: 12,
    splitRunning: false,
    onSplitActive: vi.fn(),
    mergeIdA: null,
    mergeIdB: null,
    onMergeIdA: vi.fn(),
    onMergeIdB: vi.fn(),
    mergeRunning: false,
    onMergeLabels: vi.fn(),
    deleteRunning: false,
    onDeleteActive: vi.fn(),
    ...handlers,
    ...overrides,
  };
  return { ...handlers, ...render(<AnnotateToolChrome {...props} />) };
}

describe("AnnotateToolChrome — Interpolate", () => {
  it("keeps Hard Case recording but has no standalone position-link action", () => {
    renderChrome();
    expect(screen.getByRole("button", { name: "Record hard case" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Copy position link" })).toBeNull();
  });

  it("offers Interpolate in the tool strip when the server flag is on", () => {
    const { onPaintTool } = renderChrome();
    fireEvent.click(screen.getAllByRole("button", { name: "Interpolate" })[0]);
    expect(onPaintTool).toHaveBeenCalledWith("interpolate");
  });

  it("hides Interpolate entirely when the feature is off", () => {
    renderChrome({ interpolationEnabled: false });
    expect(screen.queryByRole("button", { name: "Interpolate" })).toBeNull();
  });

  it("keeps Interpolate unavailable until both endpoints are marked", () => {
    renderChrome({ paintTool: "interpolate", interpFirst: 4, interpLast: null });
    expect(lastInterpolateButton().disabled).toBe(true);
  });

  it("enables Interpolate once the endpoints are at least two slices apart", () => {
    renderChrome({ paintTool: "interpolate", interpFirst: 4, interpLast: 9 });
    expect(lastInterpolateButton().disabled).toBe(false);
  });

  it("refuses adjacent endpoints — there is nothing in between to fill", () => {
    renderChrome({ paintTool: "interpolate", interpFirst: 4, interpLast: 5 });
    expect(lastInterpolateButton().disabled).toBe(true);
  });

  it("marks the open slice as an endpoint", () => {
    const { onInterpFirst, onInterpLast } = renderChrome({
      paintTool: "interpolate",
      currentIndex: 12,
    });
    const buttons = screen.getAllByRole("button", { name: "Use current" });
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);
    expect(onInterpFirst).toHaveBeenCalledWith(12);
    expect(onInterpLast).toHaveBeenCalledWith(12);
  });

  it("runs interpolate directly without Preview/Confirm", () => {
    const { onInterpRun } = renderChrome({
      paintTool: "interpolate",
      interpFirst: 4,
      interpLast: 9,
    });
    expect(screen.queryByRole("button", { name: "Preview" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    fireEvent.click(lastInterpolateButton());
    expect(onInterpRun).toHaveBeenCalledTimes(1);
  });

  it("shows 1-based layer numbers with Start/End layer placeholders", () => {
    renderChrome({ paintTool: "interpolate", interpFirst: 4, interpLast: 9 });
    expect(screen.getByPlaceholderText("Start layer")).toBeTruthy();
    expect(screen.getByPlaceholderText("End layer")).toBeTruthy();
    const start = screen.getByPlaceholderText("Start layer") as HTMLInputElement;
    const end = screen.getByPlaceholderText("End layer") as HTMLInputElement;
    expect(start.value).toBe("5");
    expect(end.value).toBe("10");
  });

  it("keeps the content-width Overwrite slot directly after New", () => {
    renderChrome({ paintTool: "interpolate" });
    const overwrite = screen.getByLabelText("Overwrite") as HTMLSelectElement;
    const slot = overwrite.closest(".tool-overwrite-control");

    expect(overwrite.classList.contains("tool-overwrite-select")).toBe(true);
    expect(slot?.previousElementSibling).toBe(screen.getByRole("button", { name: "New" }));
  });

  it("does not show Saved in the tool strip", () => {
    renderChrome({ status: "saved" });
    expect(screen.queryByText("Saved")).toBeNull();
  });
});

describe("AnnotateToolChrome — flood and overwrite", () => {
  it("exposes flood fill and the shared overwrite policy", () => {
    const { onPaintTool } = renderChrome({ paintTool: "flood_fill" });
    fireEvent.click(screen.getByRole("button", { name: "Flood fill" }));
    expect(onPaintTool).toHaveBeenCalledWith("flood_fill");
    const overwrite = screen.getByLabelText("Overwrite") as HTMLSelectElement;
    expect(overwrite.value).toBe("overwrite_empty");
    expect(overwrite.classList.contains("tool-overwrite-select")).toBe(true);
    expect(overwrite.closest(".tool-overwrite-control")?.previousElementSibling).toBe(
      screen.getByRole("button", { name: "New" }),
    );
    expect((screen.getByLabelText("Depth (z)") as HTMLInputElement).value).toBe("1");
    expect(screen.queryByText("Click a region to fill")).toBeNull();
  });

  it("hides flood fill when annotation tools are off", () => {
    renderChrome({ floodFillEnabled: false });
    expect(screen.queryByRole("button", { name: "Flood fill" })).toBeNull();
  });
});

describe("AnnotateToolChrome — brush/erase cursor style", () => {
  it("offers five cursor styles on Brush, defaulting to the current look", () => {
    const onCursorStyle = vi.fn();
    renderChrome({ paintTool: "brush", onCursorStyle });

    const select = screen.getByLabelText("Cursor") as HTMLSelectElement;
    expect(select.value).toBe("disc");
    expect([...select.options].map((o) => [o.value, o.text])).toEqual([
      ["disc", "Disc"],
      ["outline", "Outline"],
      ["crosshair", "Crosshair"],
      ["brackets", "Brackets"],
      ["dashed", "Dashed"],
    ]);

    fireEvent.change(select, { target: { value: "brackets" } });
    expect(onCursorStyle).toHaveBeenCalledWith("brackets");
  });

  it("offers the same control on Erase, sharing one preference", () => {
    renderChrome({ paintTool: "eraser", cursorStyle: "dashed" });
    expect((screen.getByLabelText("Cursor") as HTMLSelectElement).value).toBe("dashed");
  });

  it("keeps the cursor control out of tools that have no footprint", () => {
    renderChrome({ paintTool: "select" });
    expect(screen.queryByLabelText("Cursor")).toBeNull();
  });

  it("lets the size sliders reach a true single pixel", () => {
    renderChrome({ paintTool: "brush", brushSize: 1 });
    const slider = screen.getByTitle(/^Brush size 1/) as HTMLInputElement;
    expect(slider.min).toBe("1");
    expect(slider.title).toContain("single pixel");
  });

  it("omits the numeric brush/erase size readout while keeping Cursor", () => {
    const { container } = renderChrome({ paintTool: "brush", brushSize: 6 });
    expect(container.querySelector(".brush-size-readout")).toBeNull();
    expect(screen.getByLabelText("Cursor")).toBeTruthy();
  });
});

describe("AnnotateToolChrome — Reset labels", () => {
  it("puts Reset labels below Delete layer by using the row that already exists", () => {
    const { container } = renderChrome();
    const strip = container.querySelector(".tool-strip")!;
    const context = container.querySelector(".tool-context")!;
    const deleteLayer = screen.getByRole("button", { name: "Delete layer" });
    const reset = screen.getByRole("button", { name: "Reset labels" });

    // One button per row, each last in its own row: that is what stacks them.
    // A wrapper column would have made the tool row taller and shrunk the
    // canvas underneath it, which is the thing being avoided.
    expect(strip.contains(deleteLayer)).toBe(true);
    expect(context.contains(reset)).toBe(true);
    expect(strip.contains(reset)).toBe(false);
    expect(strip.lastElementChild).toBe(deleteLayer);
    expect(context.lastElementChild).toBe(reset);

    // "slice" is not this viewer's word for the z index any more.
    const labels = Array.from(strip.querySelectorAll("button")).map((b) => b.textContent);
    expect(labels).not.toContain("Delete slice");
  });

  it("keeps Reset labels at the end of the context row for every tool", () => {
    // The context row's contents change with the tool; this one must not, so
    // the column does not come apart when the annotator switches tool.
    for (const paintTool of ["select", "brush", "interpolate", "merge", "delete"] as PaintTool[]) {
      const { container, unmount } = renderChrome({ paintTool });
      const context = container.querySelector(".tool-context")!;
      expect(context.lastElementChild).toBe(
        screen.getByRole("button", { name: /Reset labels|Resetting/ }),
      );
      unmount();
    }
  });

  it("reserves one width for both, and adds no height to either row", () => {
    // jsdom computes no geometry, so the rules that line the two buttons up
    // are asserted against the stylesheet — same approach as `topbarLayout`.
    const deleteLayer = renderChrome().container.querySelector(".tool-strip .tool-tail-btn")!;
    expect(deleteLayer.textContent).toBe("Delete layer");

    const rule = (selector: string) => {
      const wanted = selector.replace(/\s+/g, " ").trim();
      for (const [, head, body] of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
        if (head.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s+/g, " ").trim() === wanted) {
          return body;
        }
      }
      throw new Error(`no rule for ${selector}`);
    };
    // Equal reserved widths on two equally wide rows is what aligns them.
    const tail = rule(".tool-tail-btn");
    expect(tail).toMatch(/width:\s*7rem/);
    expect(tail).toMatch(/min-width:\s*7rem/);
    expect(tail).toMatch(/max-width:\s*7rem/);
    expect(tail).toMatch(/flex:\s*0 0 auto/);
    // The trailing spacer is what holds it against the right edge.
    expect(rule(".tool-context .spacer")).toMatch(/flex:\s*1/);
    // Both rows retain fixed, slightly denser heights; Reset adds no row.
    expect(rule(".tool-strip")).toMatch(/height:\s*1\.9rem/);
    expect(rule(".tool-context")).toMatch(/height:\s*1\.52rem/);
    // And no wrapper column was introduced to hold the pair.
    expect(css).not.toContain(".tool-strip-stack");
  });

  it("says what Reset labels destroys and what it does not touch", () => {
    renderChrome();
    const reset = screen.getByRole("button", { name: "Reset labels" });
    expect(reset.getAttribute("title")).toContain("every layer");
    expect(reset.getAttribute("title")).toContain("registered source file is never changed");
  });

  it("disables only itself while a reset is running", () => {
    renderChrome({ resetRunning: true });
    expect((screen.getByRole("button", { name: "Resetting…" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Delete layer" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("hands the click straight up — the confirm lives in the canvas", () => {
    const onResetLabels = vi.fn();
    renderChrome({ onResetLabels });
    fireEvent.click(screen.getByRole("button", { name: "Reset labels" }));
    expect(onResetLabels).toHaveBeenCalledOnce();
  });
});
