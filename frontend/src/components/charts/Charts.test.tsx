import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BurndownChart, CategoryBars, Metric, ThroughputChart } from "./Charts";

describe("Metric", () => {
  it("renders a null value as an em-dash, never as zero", () => {
    // "Not measured" and "measured as zero" are different claims; the whole
    // quality and time-tracking design depends on them looking different.
    render(<Metric label="Mean Dice" value={null} />);
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.queryByText("0")).toBeNull();
  });

  it("renders a real zero as zero", () => {
    render(<Metric label="Overdue" value={0} />);
    expect(screen.getByText("0")).toBeTruthy();
  });
});

describe("ThroughputChart", () => {
  const points = [
    { date: "2026-01-01", approved: 0 },
    { date: "2026-01-02", approved: 4 },
    { date: "2026-01-03", approved: 2 },
  ];

  it("draws one mark per day, including the zero days", () => {
    // The series is zero-filled server-side, so every day must be drawn —
    // otherwise a gap would read as missing data rather than as no work.
    const { container } = render(<ThroughputChart points={points} />);
    expect(container.querySelectorAll("rect").length).toBe(3);
  });

  it("distinguishes a zero day from a worked day by class", () => {
    const { container } = render(<ThroughputChart points={points} />);
    expect(container.querySelectorAll(".chart-bar-empty").length).toBe(1);
    expect(container.querySelectorAll(".chart-bar").length).toBe(2);
  });

  it("labels only the endpoints, never every bar", () => {
    const { container } = render(<ThroughputChart points={points} />);
    // Two date labels plus the single peak-value label.
    expect(container.querySelectorAll(".chart-axis-label").length).toBe(3);
  });

  it("says so when there is nothing to draw", () => {
    render(<ThroughputChart points={[]} />);
    expect(screen.getByText("No data yet.")).toBeTruthy();
  });
});

describe("BurndownChart", () => {
  const actual = [
    { date: "2026-01-01", remaining: 10 },
    { date: "2026-01-02", remaining: 6 },
  ];
  const ideal = [
    { date: "2026-01-01", remaining: 10 },
    { date: "2026-01-02", remaining: 5 },
  ];

  it("draws the ideal as a dashed reference, not as a second series", () => {
    // Distinguishing the two by dash rather than by hue is what keeps the
    // chart readable without a colour key.
    const { container } = render(
      <BurndownChart actual={actual} ideal={ideal} />,
    );
    expect(container.querySelectorAll(".chart-reference-line").length).toBe(1);
    expect(container.querySelectorAll(".chart-line").length).toBe(1);
  });

  it("direct-labels both marks instead of using a legend box", () => {
    render(<BurndownChart actual={actual} ideal={ideal} />);
    expect(screen.getByText("Actual")).toBeTruthy();
    expect(screen.getByText("Ideal")).toBeTruthy();
  });
});

describe("CategoryBars", () => {
  it("drops empty categories and sorts by magnitude", () => {
    render(
      <CategoryBars
        rows={[
          { label: "Normal", value: 2 },
          { label: "Swollen", value: 9 },
          { label: "Donut", value: 0 },
        ]}
      />,
    );
    const labels = screen
      .getAllByText(/Normal|Swollen|Donut/)
      .map((node) => node.textContent);
    expect(labels).toEqual(["Swollen", "Normal"]);
  });

  it("shows an explicit empty message when nothing was recorded", () => {
    render(
      <CategoryBars
        rows={[{ label: "Normal", value: 0 }]}
        emptyMessage="Nothing flagged."
      />,
    );
    expect(screen.getByText("Nothing flagged.")).toBeTruthy();
  });

  it("carries identity in the row label, not in a colour key", () => {
    const { container } = render(
      <CategoryBars rows={[{ label: "Swollen", value: 3 }]} />,
    );
    expect(screen.getByText("Swollen")).toBeTruthy();
    // One fill class for every row — no per-category hue.
    expect(container.querySelectorAll(".category-bars-fill").length).toBe(1);
  });
});
