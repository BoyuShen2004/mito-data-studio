import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MitoMeasurements from "./MitoMeasurements";
import { getMeasurements, measurementCSV, runMeasurements, type MeasurementJob } from "../api/measurements";
import type { Volume } from "../types/volume";

vi.mock("../api/measurements", async importOriginal => ({
  ...await importOriginal<typeof import("../api/measurements")>(),
  getMeasurements: vi.fn(), runMeasurements: vi.fn(),
}));
const volume = { id: 3, has_label: true, voxel_size_z: 30, voxel_size_y: 16, voxel_size_x: 16 } as Volume;
const done: MeasurementJob = {
  id: 4, source: "official", status: "succeeded", created_at: "2026-10-02T00:00:00Z",
  finished_at: "2026-10-02T00:01:00Z", error: "", is_current: true,
  result: {
    rows: [{ label_id: 5, voxel_count: 3600, volume_um3: 0.027648, skeleton_length_um: 1.6 }],
    measured_at: "2026-10-02T00:01:00Z", source: "official", voxel_size_nm_zyx: [30, 16, 16],
    dust_size_voxels: 100, method: "TEASAR (kimimaro)", scope: "Whole volume",
  },
};
beforeEach(() => {
  vi.mocked(getMeasurements).mockReset().mockResolvedValue({ job: null });
  vi.mocked(runMeasurements).mockReset();
});

describe("MitoMeasurements", () => {
  it("does not start computing on mount and explicitly queues the selected source", async () => {
    vi.mocked(runMeasurements).mockResolvedValue({ job: { ...done, status: "queued", result: null } });
    render(<MitoMeasurements volume={volume} canRun />);
    await screen.findByText(/No measurements have been run/);
    expect(runMeasurements).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "working" } });
    await screen.findByText(/No measurements have been run/);
    expect(screen.getByText(/unsaved canvas edits are excluded/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Run measurements" }));
    await waitFor(() => expect(runMeasurements).toHaveBeenCalledWith(3, "working"));
  });

  it("displays measured units, zero-length caveat and historical provenance", async () => {
    vi.mocked(getMeasurements).mockResolvedValue({ job: { ...done, is_current: false } });
    render(<MitoMeasurements volume={volume} canRun />);
    await screen.findByRole("table");
    expect(screen.getByText("Volume (µm³)")).toBeTruthy();
    expect(screen.getByText("Skeleton cable length (µm)")).toBeTruthy();
    expect(screen.getByText("0.027648")).toBeTruthy();
    expect(screen.getByText(/Historical result/)).toBeTruthy();
    expect(screen.getByText(/zero cable length/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeTruthy();
  });

  it("keeps unknown voxel spacing unknown and disables running", async () => {
    render(<MitoMeasurements volume={{ ...volume, voxel_size_y: null }} canRun />);
    await screen.findByText(/No measurements have been run/);
    expect(screen.getByText(/Set all three positive voxel sizes/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Run measurements" }) as HTMLButtonElement).disabled).toBe(true);
    expect(runMeasurements).not.toHaveBeenCalled();
  });

  it("prevents running without an official label", async () => {
    render(<MitoMeasurements volume={{ ...volume, has_label: false }} canRun />);
    await screen.findByText(/No measurements have been run/);
    expect((screen.getByRole("button", { name: "Run measurements" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("No official label is registered.")).toBeTruthy();
  });

  it("allows members to read results without manager run controls", async () => {
    vi.mocked(getMeasurements).mockResolvedValue({ job: done });
    render(<MitoMeasurements volume={volume} canRun={false} />);
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: "Run measurements" })).toBeNull();
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeTruthy();
  });

  it("reports a failed job without fabricating results", async () => {
    vi.mocked(getMeasurements).mockResolvedValue({ job: { ...done, status: "failed", result: null, error: "Labels changed during measurement." } });
    render(<MitoMeasurements volume={volume} canRun />);
    expect((await screen.findByRole("alert")).textContent).toBe("Labels changed during measurement.");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("polls an active job to completion", async () => {
    vi.mocked(getMeasurements)
      .mockResolvedValueOnce({ job: { ...done, status: "submitted", result: null } })
      .mockResolvedValueOnce({ job: done });
    render(<MitoMeasurements volume={volume} canRun />);
    await screen.findByText(/Run #4 · submitted/);
    expect((screen.getByRole("button", { name: "Measurement in progress…" }) as HTMLButtonElement).disabled).toBe(true);
    await screen.findByRole("table", {}, { timeout: 4500 });
    expect(getMeasurements).toHaveBeenCalledTimes(2);
  });

  it("exports full precision with source, spacing and run identity", () => {
    const csv = measurementCSV(3, done);
    expect(csv).toContain("volume_um3,skeleton_length_um");
    expect(csv).toContain("3,4,official,2026-10-02T00:01:00Z,30,16,16,100,5,3600,0.027648,1.6");
  });
});
