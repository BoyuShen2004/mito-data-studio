import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import ProjectMeasurements from "./ProjectMeasurements";
import { editVolume } from "../api/volumes";
import { getMeasurementSpacing, getMeasurements, runMeasurements } from "../api/measurements";
import type { Volume } from "../types/volume";
vi.mock("../api/volumes", () => ({ editVolume: vi.fn() }));
vi.mock("../api/measurements", () => ({ getMeasurementSpacing: vi.fn(), getMeasurements: vi.fn(), runMeasurements: vi.fn(), measurementCSV: vi.fn() }));
const volume = { id: 4, name: "Volume A", has_label: true, voxel_size_z: null, voxel_size_y: null, voxel_size_x: null } as Volume;
const open = (canRun = true) => render(<MemoryRouter initialEntries={["/projects/1?tab=measurements&volume=4"]}>
  <ProjectMeasurements volumes={[{ ...volume, id: 3, name: "Volume B" }, volume]} loading={false} error={null} canRun={canRun} onSaved={vi.fn()} />
</MemoryRouter>);
beforeEach(() => { vi.clearAllMocks(); vi.mocked(getMeasurementSpacing).mockResolvedValue({ voxel_size_um_zyx: [null, null, null], origins: ["unknown", "unknown", "unknown"] }); vi.mocked(getMeasurements).mockResolvedValue({ job: null }); });
it("requires actual spacing and explicit metadata save before a run", async () => {
  vi.mocked(editVolume).mockResolvedValue({ ...volume, voxel_size_z: 0.030, voxel_size_y: 0.016, voxel_size_x: 0.016 });
  open();
  await screen.findByText(/No runs for this label source/);
  expect((screen.getByLabelText("Volume") as HTMLSelectElement).value).toBe("4");
  expect((screen.getByLabelText("Z (nm)") as HTMLInputElement).value).toBe("");
  for (const [axis, value] of [["Z", "30"], ["Y", "16"], ["X", "16"]]) {
    fireEvent.change(screen.getByLabelText(`${axis} (nm)`), { target: { value } });
  }
  expect((screen.getByRole("button", { name: "Run measurements" }) as HTMLButtonElement).disabled).toBe(true);
  expect(editVolume).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save voxel size" }));
  await waitFor(() => expect(editVolume).toHaveBeenCalledWith(4, { voxel_size_z: 0.030, voxel_size_y: 0.016, voxel_size_x: 0.016 }));
  await waitFor(() => expect((screen.getByRole("button", { name: "Run measurements" }) as HTMLButtonElement).disabled).toBe(false));
  expect(runMeasurements).not.toHaveBeenCalled();
});
it("does not copy spacing to a different volume", async () => {
  open(); await screen.findByText(/No runs for this label source/);
  fireEvent.change(screen.getByLabelText("Z (nm)"), { target: { value: "30" } });
  fireEvent.change(screen.getByLabelText("Volume"), { target: { value: "3" } });
  expect((screen.getByLabelText("Z (nm)") as HTMLInputElement).value).toBe("");
  await waitFor(() => expect(getMeasurements).toHaveBeenCalledWith(3, "official"));
  expect(editVolume).not.toHaveBeenCalled();
});
it("keeps metadata editing and run controls manager-only", async () => {
  open(false); await screen.findByText(/No runs for this label source/);
  expect(screen.queryByRole("button", { name: "Save voxel size" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Run measurements" })).toBeNull();
});
it("keeps invalid spacing and failed saves from enabling measurement", async () => {
  vi.mocked(editVolume).mockRejectedValue(new Error("Metadata save failed"));
  open(); await screen.findByText(/No runs for this label source/);
  for (const axis of ["Z", "Y", "X"]) fireEvent.change(screen.getByLabelText(`${axis} (nm)`), { target: { value: "0" } });
  expect((screen.getByRole("button", { name: "Save voxel size" }) as HTMLButtonElement).disabled).toBe(true);
  for (const axis of ["Z", "Y", "X"]) fireEvent.change(screen.getByLabelText(`${axis} (nm)`), { target: { value: "16" } });
  fireEvent.click(screen.getByRole("button", { name: "Save voxel size" }));
  expect((await screen.findByRole("alert")).textContent).toBe("Metadata save failed");
  expect((screen.getByRole("button", { name: "Run measurements" }) as HTMLButtonElement).disabled).toBe(true);
});

it("automatically reads source spacing and can run without saving or guessing metadata", async () => {
  vi.mocked(getMeasurementSpacing).mockResolvedValue({ voxel_size_um_zyx: [0.03, 0.016, 0.016], origins: ["source_file", "source_file", "source_file"] });
  open();
  await screen.findByText(/Spacing from source metadata/);
  expect((screen.getByLabelText("Z (nm)") as HTMLInputElement).value).toBe("30");
  expect((screen.getByLabelText("X (nm)") as HTMLInputElement).value).toBe("16");
  await waitFor(() => expect((screen.getByRole("button", { name: "Run measurements" }) as HTMLButtonElement).disabled).toBe(false));
  expect(editVolume).not.toHaveBeenCalled();
  expect(runMeasurements).not.toHaveBeenCalled();
});
it("allows manual fallback if metadata detection fails", async () => {
  vi.mocked(getMeasurementSpacing).mockRejectedValue(new Error("Unreadable"));
  open();
  await screen.findByText(/Metadata unavailable/);
  expect((screen.getByLabelText("Z (nm)") as HTMLInputElement).disabled).toBe(false);
  expect((screen.getByRole("button", { name: "Run measurements" }) as HTMLButtonElement).disabled).toBe(true);
});

it("preserves the chosen draft source through a spacing save and volume-list refresh", async () => {
  const updated = { ...volume, voxel_size_z: 0.03, voxel_size_y: 0.016, voxel_size_x: 0.016 };
  vi.mocked(editVolume).mockResolvedValue(updated);
  const onSaved = vi.fn();
  const content = (volumes: Volume[], loading = false, error: string | null = null) =>
    <MemoryRouter initialEntries={["/projects/1?tab=measurements&volume=4"]}>
      <ProjectMeasurements volumes={volumes} loading={loading} error={error} canRun onSaved={onSaved} />
    </MemoryRouter>;
  const view = render(content([volume]));
  await screen.findByText(/No runs for this label source/);
  fireEvent.change(screen.getByLabelText("Label source"), { target: { value: "working" } });
  await waitFor(() => expect(getMeasurements).toHaveBeenCalledWith(4, "working"));
  const source = screen.getByLabelText("Label source") as HTMLSelectElement;
  for (const [axis, value] of [["Z", "30"], ["Y", "16"], ["X", "16"]]) {
    fireEvent.change(screen.getByLabelText(`${axis} (nm)`), { target: { value } });
  }
  fireEvent.click(screen.getByRole("button", { name: "Save voxel size" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(source.value).toBe("working");
  view.rerender(content([volume], true));
  expect(source.isConnected).toBe(true);
  vi.mocked(getMeasurementSpacing).mockResolvedValue({ voxel_size_um_zyx: [0.03, 0.016, 0.016], origins: ["registered", "registered", "registered"] });
  view.rerender(content([updated]));
  await waitFor(() => expect(screen.getByRole("button", { name: "Run measurements" }).hasAttribute("disabled")).toBe(false));
  expect(screen.getByLabelText("Label source")).toBe(source);
  expect(source.value).toBe("working");
  expect(runMeasurements).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Run measurements" }));
  await waitFor(() => expect(runMeasurements).toHaveBeenCalledWith(4, "working"));
});

it("retains pending spacing and source through an unrelated refresh or refresh error", async () => {
  const content = (loading: boolean, error: string | null = null) =>
    <MemoryRouter><ProjectMeasurements volumes={[{ ...volume }]} loading={loading} error={error} canRun onSaved={vi.fn()} /></MemoryRouter>;
  const view = render(content(false));
  await screen.findByText(/No runs for this label source/);
  fireEvent.change(screen.getByLabelText("Z (nm)"), { target: { value: "45" } });
  fireEvent.change(screen.getByLabelText("Label source"), { target: { value: "working" } });
  view.rerender(content(true));
  expect((screen.getByLabelText("Z (nm)") as HTMLInputElement).value).toBe("45");
  view.rerender(content(false, "Could not refresh volumes"));
  expect(screen.getByRole("alert").textContent).toBe("Could not refresh volumes");
  expect((screen.getByLabelText("Z (nm)") as HTMLInputElement).value).toBe("45");
  expect((screen.getByLabelText("Label source") as HTMLSelectElement).value).toBe("working");
  expect(getMeasurementSpacing).toHaveBeenCalledOnce();
});
