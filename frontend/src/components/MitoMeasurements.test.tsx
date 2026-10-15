import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MitoMeasurements from "./MitoMeasurements";
import { getMeasurements, measurementCSV, runMeasurements, type MeasurementJob, type MeasurementResult } from "../api/measurements";
import type { Volume } from "../types/volume";

vi.mock("../api/measurements", async importOriginal => ({
  ...await importOriginal<typeof import("../api/measurements")>(),
  getMeasurements: vi.fn(), runMeasurements: vi.fn(),
}));
const volume = { id: 3, has_label: true, voxel_size_z: 30, voxel_size_y: 16, voxel_size_x: 16 } as Volume;
// Saved before skip/edge reporting existed: rows have neither field.
const doneResult: MeasurementResult = {
  rows: [{ label_id: 5, voxel_count: 3600, volume_um3: 0.027648, skeleton_length_um: 1.6 }],
  measured_at: "2026-10-02T00:01:00Z", source: "official", voxel_size_nm_zyx: [30, 16, 16],
  dust_size_voxels: 100, method: "TEASAR (kimimaro)", scope: "Whole volume",
};
const done: MeasurementJob = {
  id: 4, source: "official", status: "succeeded", created_at: "2026-10-02T00:00:00Z",
  finished_at: "2026-10-02T00:01:00Z", error: "", is_current: true,
  result: doneResult,
};
const mixed: MeasurementJob = {
  ...done,
  result: {
    ...doneResult,
    rows: [
      { label_id: 5, voxel_count: 3600, volume_um3: 0.027648, skeleton_length_um: 1.6, touches_edge: false, skipped_reason: null },
      { label_id: 6, voxel_count: 40000000, volume_um3: 307.2, skeleton_length_um: null, touches_edge: true, skipped_reason: "crop_limit" },
      { label_id: 7, voxel_count: 5000, volume_um3: 0.0384, skeleton_length_um: 2.25, touches_edge: true, skipped_reason: null },
    ],
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
    await screen.findByText(/No runs for this label source/);
    expect(runMeasurements).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "working" } });
    await screen.findByText(/No runs for this label source/);
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
    expect(screen.getByText(/Outdated result/)).toBeTruthy();
    expect(screen.getByText(/Zero length may indicate filtering/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeTruthy();
  });

  it("shows skipped lengths and edge flags without dropping measured rows", async () => {
    vi.mocked(getMeasurements).mockResolvedValue({ job: mixed });
    render(<MitoMeasurements volume={volume} canRun />);
    await screen.findByRole("table");
    expect(screen.getByText("Touches edge")).toBeTruthy();
    expect(screen.getByText("Skipped (too large)")).toBeTruthy();
    expect(screen.getByText("2.25")).toBeTruthy();
    expect(screen.getAllByText("Yes")).toHaveLength(2);
    expect(screen.getAllByText("No")).toHaveLength(1);
    expect(screen.getByText(/1 skipped for length/)).toBeTruthy();
    expect(screen.getByText(/2 touch the volume edge/)).toBeTruthy();
  });

  it("marks edge status unknown for results saved before edge detection", async () => {
    vi.mocked(getMeasurements).mockResolvedValue({ job: done });
    render(<MitoMeasurements volume={volume} canRun />);
    await screen.findByRole("table");
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.queryByText("Yes")).toBeNull();
    expect(screen.queryByText("No")).toBeNull();
    expect(screen.queryByText(/skipped for length/)).toBeNull();
    expect(screen.queryByText(/touch the volume edge/)).toBeNull();
  });

  it("labels a skip with an unrecognised reason generically", async () => {
    vi.mocked(getMeasurements).mockResolvedValue({ job: { ...done, result: { ...doneResult, rows: [
      { label_id: 8, voxel_count: 900, volume_um3: 0.006912, skeleton_length_um: null, touches_edge: false, skipped_reason: "other" },
    ] } } });
    render(<MitoMeasurements volume={volume} canRun />);
    await screen.findByRole("table");
    expect(screen.getByText("Skipped")).toBeTruthy();
    expect(screen.queryByText("Skipped (too large)")).toBeNull();
  });

  it("keeps unknown voxel spacing unknown and disables running", async () => {
    render(<MitoMeasurements volume={{ ...volume, voxel_size_y: null }} canRun />);
    await screen.findByText(/No runs for this label source/);
    expect(screen.getByText(/Voxel size required/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Run measurements" }) as HTMLButtonElement).disabled).toBe(true);
    expect(runMeasurements).not.toHaveBeenCalled();
  });

  it("prevents running without an official label", async () => {
    render(<MitoMeasurements volume={{ ...volume, has_label: false }} canRun />);
    await screen.findByText(/No runs for this label source/);
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

  it("exports skip and edge columns, leaving a skipped length empty", () => {
    const lines = measurementCSV(3, mixed).trimEnd().split("\n");
    expect(lines[0].endsWith(",skeleton_length_um,touches_edge,skipped_reason")).toBe(true);
    expect(lines.slice(1)).toEqual([
      "3,4,official,2026-10-02T00:01:00Z,30,16,16,100,5,3600,0.027648,1.6,false,",
      "3,4,official,2026-10-02T00:01:00Z,30,16,16,100,6,40000000,307.2,,true,crop_limit",
      "3,4,official,2026-10-02T00:01:00Z,30,16,16,100,7,5000,0.0384,2.25,true,",
    ]);
  });

  it("exports unknown edge status as empty for results saved before edge detection", () => {
    const lines = measurementCSV(3, done).trimEnd().split("\n");
    expect(lines[1]).toBe("3,4,official,2026-10-02T00:01:00Z,30,16,16,100,5,3600,0.027648,1.6,,");
  });
});

it("refetches result freshness when voxel spacing changes without resetting the source", async () => {
  vi.mocked(getMeasurements).mockResolvedValue({ job: done });
  const view = render(<MitoMeasurements volume={volume} canRun />);
  await screen.findByRole('table');
  fireEvent.change(screen.getByLabelText('Label source'), { target: { value: 'working' } });
  await waitFor(() => expect(getMeasurements).toHaveBeenCalledWith(3, 'working'));
  await screen.findByRole('table');
  vi.mocked(getMeasurements).mockClear().mockResolvedValue({ job: { ...done, source: 'working', is_current: false } });
  view.rerender(<MitoMeasurements volume={{ ...volume, voxel_size_z: 31 }} canRun />);
  await screen.findByText(/Outdated result/);
  expect(getMeasurements).toHaveBeenCalledWith(3, 'working');
  expect((screen.getByLabelText('Label source') as HTMLSelectElement).value).toBe('working');
  expect(runMeasurements).not.toHaveBeenCalled();
});

it("clears old freshness when new spacing results cannot be fetched", async () => {
  vi.mocked(getMeasurements).mockResolvedValue({ job: done });
  const view = render(<MitoMeasurements volume={volume} canRun />);
  await screen.findByRole("table");
  vi.mocked(getMeasurements).mockRejectedValueOnce(new Error("Results unavailable"));
  view.rerender(<MitoMeasurements volume={{ ...volume, voxel_size_z: 31 }} canRun />);
  expect((await screen.findByRole("alert")).textContent).toBe("Results unavailable");
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByRole("button", { name: "Export CSV" })).toBeNull();
  vi.mocked(getMeasurements).mockResolvedValue({ job: { ...done, is_current: false } });
  fireEvent.click(screen.getByRole("button", { name: "Refresh results" }));
  await screen.findByText(/Outdated result/);
  expect(screen.getByRole("table")).toBeTruthy();
  expect(runMeasurements).not.toHaveBeenCalled();
});
