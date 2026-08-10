import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { LabelSummaryRow } from "../../api/viewer";
import LabelsPanel, { type LabelsScope } from "./LabelsPanel";

const rows: LabelSummaryRow[] = [1, 2, 3].map((id) => ({
  id,
  voxel_count: id * 10,
  z_start: id,
  z_end: id,
  state: "edited",
  origin: "manual",
  verified_at: "",
}));
const largeRows: LabelSummaryRow[] = Array.from({ length: 1001 }, (_, index) => ({
  ...rows[0],
  id: index + 1,
  voxel_count: index + 1,
}));
const onRefresh = vi.fn();

function Harness({
  initialScope = "slice",
  region = true,
  data = rows,
  targetId,
  readOnly = false,
  soloId = null,
  onResetVisibility = vi.fn(),
  onLifecycleAction = vi.fn(),
  pinnedIds = new Set<number>(),
  onClearPins = vi.fn(),
  pinActiveToTopToken = 0,
}: {
  initialScope?: LabelsScope;
  region?: boolean;
  data?: LabelSummaryRow[];
  targetId?: number;
  readOnly?: boolean;
  soloId?: number | null;
  onResetVisibility?: () => void;
  onLifecycleAction?: (id: number, action: "verify" | "unverify" | "revert" | "reject") => void;
  pinnedIds?: Set<number>;
  onClearPins?: () => void;
  pinActiveToTopToken?: number;
}) {
  const [scope, setScope] = useState<LabelsScope>(initialScope);
  const [activeId, setActiveId] = useState(1);
  return <>{targetId != null && <button onClick={() => setActiveId(targetId)}>Activate target</button>}<LabelsPanel
    scope={scope} onScopeChange={setScope} activeId={activeId} onSetActiveId={setActiveId}
    sliceInstances={data.map((row) => row.id)} rows={data} rowsLoading={false}
    hiddenIds={new Set()} soloId={soloId} onToggleHidden={vi.fn()} onToggleSolo={vi.fn()}
    onResetVisibility={onResetVisibility} pinnedIds={pinnedIds} onTogglePinned={vi.fn()}
    onPinMany={vi.fn()} onClearPins={onClearPins} onJumpToZ={vi.fn()} hideVerified={false}
    onHideVerifiedChange={vi.fn()} hasRegionMask={region} hideOutsideRegion={false}
    onHideOutsideRegionChange={vi.fn()} regionMemberIds={new Set(data.map((row) => row.id))}
    onLifecycleAction={onLifecycleAction} onRefresh={onRefresh} readOnly={readOnly}
    pinActiveToTopToken={pinActiveToTopToken}
  /></>;
}

const rowIds = (container: HTMLElement) => Array.from(container.querySelectorAll(".labels-list li"))
  .map((element) => Number(element.textContent?.trim().match(/^\d+/)?.[0]));

describe("LabelsPanel list chrome and selection", () => {
  beforeEach(() => {
    onRefresh.mockReset();
    Object.defineProperty(globalThis, "ResizeObserver", {
      configurable: true,
      value: class ResizeObserver {
        observe() {}
        disconnect() {}
      },
    });
  });

  it("keeps filter controls together in fixed chrome and rows in their own scrollport", () => {
    const { container } = render(<Harness />);
    const filterRow = container.querySelector(".labels-filters-header-left")!;
    expect(filterRow.textContent).toContain("Filters Options");
    expect(filterRow.textContent).toContain("Hide Verified");
    expect(filterRow.textContent).toContain("Hide non-ROI");
    const chrome = container.querySelector(".labels-panel-chrome")!;
    const titleRow = container.querySelector(".labels-panel-chrome > .row.spread")!;
    const scroller = container.querySelector(".labels-list-scroll")!;
    expect(chrome.contains(screen.getByText("Labels"))).toBe(true);
    expect(titleRow.contains(screen.getByRole("button", { name: "Refresh" }))).toBe(true);
    expect(chrome.contains(screen.getByPlaceholderText(/Search label ID/))).toBe(true);
    expect(scroller.querySelector(".labels-list")).toBeTruthy();
    expect(scroller.querySelector(".labels-list-meta")).toBeTruthy();
    expect(chrome.contains(scroller)).toBe(false);
    // Count / 3D all live inside the scrollport (sticky ceiling), not chrome.
    expect(chrome.contains(scroller.querySelector(".labels-list-meta")!)).toBe(false);
  });

  it("highlights a list click in place without moving the scrollbar", () => {
    const { container } = render(<Harness />);
    expect(rowIds(container)).toEqual([1, 2, 3]);
    const scroller = container.querySelector<HTMLElement>(".labels-list-scroll")!;
    scroller.scrollTop = 40;
    fireEvent.click(container.querySelectorAll(".labels-list li > span:first-child")[1]);
    expect(rowIds(container)).toEqual([1, 2, 3]);
    expect(container.querySelectorAll(".labels-list li")[1].classList).toContain("labels-row-active");
    // List click: highlight only — keep the user's scroll position.
    expect(scroller.scrollTop).toBe(40);

    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(rowIds(container)).toEqual([1, 2, 3]);
    const row3 = Array.from(container.querySelectorAll(".labels-list li"))
      .find((element) => element.textContent?.trim().startsWith("3"));
    fireEvent.click(row3!.querySelector("span")!);
    expect(rowIds(container)).toEqual([1, 2, 3]);
  });

  it("has no Revert/Delete/Reject control and keeps Refresh out of Filters Options", () => {
    const { container } = render(<Harness />);
    expect(screen.queryByTitle(/Delete this instance/)).toBeNull();
    expect(screen.queryByText("🗑")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Filters Options/ }));
    expect(screen.queryByRole("button", { name: /Reject/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Revert/i })).toBeNull();
    const popup = container.querySelector(".labels-filters-popup")!;
    expect(popup.textContent).not.toContain("Refresh");
    for (const name of [/^✓ Verify$/, /^○ Unverify$/, /^Solo$/, /^Show All$/]) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it("keeps the same header Refresh in read-only mode", () => {
    const { container } = render(<Harness readOnly />);
    const titleRow = container.querySelector(".labels-panel-chrome > .row.spread")!;
    const refresh = screen.getByRole("button", { name: "Refresh" });
    expect(titleRow.contains(refresh)).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Filters Options/ }));
    expect(container.querySelector(".labels-filters-popup")!.textContent).not.toContain("Refresh");
    fireEvent.click(refresh);
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it("names the header visibility control Show all, not Reset", () => {
    const onResetVisibility = vi.fn();
    const { container, rerender } = render(<Harness />);
    const titleRow = () => container.querySelector(".labels-panel-chrome > .row.spread")!;
    // Nothing soloed or hidden: no control at all.
    expect(titleRow().textContent).not.toContain("Show all");

    rerender(<Harness soloId={2} onResetVisibility={onResetVisibility} />);
    // "Reset" here read as Annotate's "Reset labels" / Assign's "Reset
    // annotations", which discard work — this one only clears filters.
    expect(screen.queryByRole("button", { name: "Reset" })).toBeNull();
    const showAll = screen.getByRole("button", { name: "Show all" });
    const refresh = screen.getByRole("button", { name: "Refresh" });
    expect(titleRow().contains(showAll)).toBe(true);
    expect(showAll.getAttribute("title")).toBe("Clear solo / hidden filters");
    // Show all sits left of Refresh when both are present.
    expect(
      showAll.compareDocumentPosition(refresh) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    fireEvent.click(showAll);
    expect(onResetVisibility).toHaveBeenCalledOnce();
  });

  it("offers row-specific Verify and Unverify on right click", () => {
    const onLifecycleAction = vi.fn();
    const data = [
      { ...rows[0], id: 1, state: "edited" as const },
      { ...rows[1], id: 2, state: "verified" as const },
    ];
    const { container } = render(
      <Harness data={data} onLifecycleAction={onLifecycleAction} />,
    );
    const listRows = container.querySelectorAll(".labels-list li");
    fireEvent.contextMenu(listRows[0], { clientX: 20, clientY: 20 });
    const menu = screen.getByRole("menu", { name: "Label 1 actions" });
    expect(menu.textContent).not.toMatch(/3D|Solo/i);
    expect(menu.querySelectorAll("button")).toHaveLength(2);
    expect((screen.getByRole("button", { name: "Verify" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "Unverify" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(onLifecycleAction).toHaveBeenCalledWith(1, "verify");

    fireEvent.contextMenu(listRows[1], { clientX: 20, clientY: 20 });
    fireEvent.click(screen.getByRole("button", { name: "Unverify" }));
    expect(onLifecycleAction).toHaveBeenCalledWith(2, "unverify");
  });

  it("offers Clear 3D beside the scoped bulk action and disables it when empty", () => {
    const onClearPins = vi.fn();
    const { rerender } = render(<Harness onClearPins={onClearPins} />);
    const clear = screen.getByRole("button", { name: "Clear 3D" });
    expect(screen.getByRole("button", { name: "3D layer" })).toBeTruthy();
    expect((clear as HTMLButtonElement).disabled).toBe(true);

    rerender(<Harness pinnedIds={new Set([1])} onClearPins={onClearPins} />);
    fireEvent.click(screen.getByRole("button", { name: "Clear 3D" }));
    expect(onClearPins).toHaveBeenCalledOnce();
  });

  it("scrolls a 1000+ row virtual All list by sorted index only when pinning from canvas token", () => {
    // List-row activation must not move the scrollport; canvas pin token does.
    const { container, rerender } = render(
      <Harness initialScope="all" data={largeRows} targetId={900} />,
    );
    const scroller = container.querySelector<HTMLElement>(".labels-list-scroll")!;
    expect(scroller.scrollTop).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Activate target" }));
    expect(scroller.scrollTop).toBe(0);

    rerender(
      <Harness initialScope="all" data={largeRows} targetId={900} pinActiveToTopToken={1} />,
    );
    // Pending-pin retry settles after the virtual window catches the target.
    rerender(
      <Harness initialScope="all" data={largeRows} targetId={900} pinActiveToTopToken={1} />,
    );
    expect(scroller.scrollTop).toBeGreaterThan(20_000);
    fireEvent.scroll(scroller);
    const renderedIds = rowIds(container).filter(Number.isFinite);
    expect(renderedIds).toContain(900);
    expect(renderedIds).toEqual([...renderedIds].sort((a, b) => a - b));
  });
});
