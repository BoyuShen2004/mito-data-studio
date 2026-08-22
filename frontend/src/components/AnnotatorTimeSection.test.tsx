import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getAnnotatorTime = vi.fn();
vi.mock("../api/timing", () => ({ getAnnotatorTime: (u: string) => getAnnotatorTime(u) }));

import AnnotatorTimeSection from "./AnnotatorTimeSection";

const report = {
  annotator: "ann",
  seconds: 8040,
  display: "2h 14m",
  legacy_volumes: 1,
  has_legacy: true,
  projects: [
    {
      project_id: 1,
      project_title: "Cortex",
      seconds: 8040,
      display: "2h 14m",
      legacy_volumes: 1,
      has_legacy: true,
      datasets: [
        {
          dataset_id: 7,
          dataset_name: "ds-a",
          seconds: 8040,
          display: "2h 14m",
          legacy_volumes: 1,
          has_legacy: true,
          volumes: [
            { volume_id: 11, volume_name: "v-measured", tracked: true, seconds: 8040, display: "2h 14m" },
            { volume_id: 12, volume_name: "v-idle", tracked: true, seconds: 0, display: "0m" },
            { volume_id: 13, volume_name: "v-legacy", tracked: false, seconds: null, display: "-" },
          ],
        },
      ],
    },
  ],
};

const toggle = (name: RegExp | string) =>
  screen.getByRole("button", { name }) as HTMLButtonElement;

describe("AnnotatorTimeSection", () => {
  beforeEach(() => {
    getAnnotatorTime.mockReset();
    getAnnotatorTime.mockResolvedValue(report);
  });

  it("fetches nothing until it is expanded", () => {
    render(<AnnotatorTimeSection username="ann" />);
    expect(getAnnotatorTime).not.toHaveBeenCalled();
    expect(toggle(/Time/).getAttribute("aria-expanded")).toBe("false");
  });

  it("loads once on expand and not again on collapse/expand", async () => {
    render(<AnnotatorTimeSection username="ann" />);
    fireEvent.click(toggle(/Time/));
    await waitFor(() => expect(screen.getByText("Cortex")).toBeTruthy());
    expect(getAnnotatorTime).toHaveBeenCalledTimes(1);
    fireEvent.click(toggle(/Time/));
    fireEvent.click(toggle(/Time/));
    await waitFor(() => expect(screen.getByText("Cortex")).toBeTruthy());
    expect(getAnnotatorTime).toHaveBeenCalledTimes(1);
  });

  it("drills annotator → project → dataset → volume", async () => {
    render(<AnnotatorTimeSection username="ann" />);
    fireEvent.click(toggle(/Time/));
    await waitFor(() => expect(screen.getByText("Cortex")).toBeTruthy());

    // Datasets are hidden until the project is opened.
    expect(screen.queryByText("ds-a")).toBeNull();
    fireEvent.click(toggle(/Cortex/));
    expect(screen.getByText("ds-a")).toBeTruthy();

    // Volumes are hidden until the dataset is opened.
    expect(screen.queryByText("v-measured")).toBeNull();
    fireEvent.click(toggle(/ds-a/));
    expect(screen.getByText("v-measured")).toBeTruthy();
    expect(screen.getByText("v-idle")).toBeTruthy();
    expect(screen.getByText("v-legacy")).toBeTruthy();
  });

  it("shows a real zero and an unknown as different things", async () => {
    render(<AnnotatorTimeSection username="ann" />);
    fireEvent.click(toggle(/Time/));
    await waitFor(() => expect(screen.getByText("Cortex")).toBeTruthy());
    fireEvent.click(toggle(/Cortex/));
    fireEvent.click(toggle(/ds-a/));

    const row = (name: string) =>
      screen.getByText(name).closest("li") as HTMLElement;
    // Measured but never opened: a genuine zero.
    expect(row("v-idle").textContent).toContain("0m");
    // Legacy: unknown, muted, and explained on hover — never counted as zero.
    expect(row("v-legacy").textContent).toContain("-");
    expect(row("v-legacy").textContent).not.toContain("0m");
    const unknown = row("v-legacy").querySelector(".annotation-time-unknown")!;
    expect(unknown.getAttribute("title")).toMatch(/before time tracking/);
  });

  it("says a partly-legacy total is incomplete rather than implying it is whole", async () => {
    render(<AnnotatorTimeSection username="ann" />);
    fireEvent.click(toggle(/Time/));
    await waitFor(() => expect(screen.getByText("Cortex")).toBeTruthy());
    // Root, project and dataset each carry the marker.
    expect(screen.getAllByText("+ legacy").length).toBeGreaterThanOrEqual(2);
    const marker = screen.getAllByText("+ legacy")[0];
    expect(marker.getAttribute("title")).toMatch(/unknown and not included/);
  });

  it("omits the marker when everything underneath is measured", async () => {
    getAnnotatorTime.mockResolvedValue({
      ...report,
      legacy_volumes: 0,
      has_legacy: false,
      projects: [{ ...report.projects[0], legacy_volumes: 0, has_legacy: false }],
    });
    render(<AnnotatorTimeSection username="ann" />);
    fireEvent.click(toggle(/Time/));
    await waitFor(() => expect(screen.getByText("Cortex")).toBeTruthy());
    expect(screen.queryByText("+ legacy")).toBeNull();
  });

  it("reports a failure without breaking the page", async () => {
    getAnnotatorTime.mockRejectedValue(new Error("nope"));
    render(<AnnotatorTimeSection username="ann" />);
    fireEvent.click(toggle(/Time/));
    await waitFor(() => expect(screen.getByText("nope")).toBeTruthy());
  });

  it("says so when there is nothing recorded", async () => {
    getAnnotatorTime.mockResolvedValue({
      annotator: "ann", seconds: 0, display: "0m",
      legacy_volumes: 0, has_legacy: false, projects: [],
    });
    render(<AnnotatorTimeSection username="ann" />);
    fireEvent.click(toggle(/Time/));
    await waitFor(() =>
      expect(screen.getByText("No annotation time recorded.")).toBeTruthy(),
    );
  });

  it("uses accessible expandable controls throughout", async () => {
    render(<AnnotatorTimeSection username="ann" />);
    fireEvent.click(toggle(/Time/));
    await waitFor(() => expect(screen.getByText("Cortex")).toBeTruthy());
    fireEvent.click(toggle(/Cortex/));
    // A level that has not been opened must say so, not merely look closed.
    expect(toggle(/ds-a/).getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle(/ds-a/));
    for (const name of [/Time/, /Cortex/, /ds-a/]) {
      expect(toggle(name).tagName).toBe("BUTTON");
      expect(toggle(name).getAttribute("aria-expanded")).toBe("true");
    }
  });
});
