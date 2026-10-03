import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import ProjectMeasurements from "./ProjectMeasurements";
import { editVolume } from "../api/volumes";
import { getMeasurements, runMeasurements } from "../api/measurements";
import type { Volume } from "../types/volume";
vi.mock("../api/volumes", () => ({ editVolume: vi.fn() }));
vi.mock("../api/measurements", () => ({ getMeasurements: vi.fn(), runMeasurements: vi.fn(), measurementCSV: vi.fn() }));
const volume = { id: 4, name: "Volume A", has_label: true, voxel_size_z: null, voxel_size_y: null, voxel_size_x: null } as Volume;
const open = (canRun = true) => render(<MemoryRouter initialEntries={["/projects/1?tab=measurements&volume=4"]}>
  <ProjectMeasurements volumes={[{ ...volume, id: 3, name: "Volume B" }, volume]} loading={false} error={null} canRun={canRun} onSaved={vi.fn()} />
</MemoryRouter>);
beforeEach(() => { vi.clearAllMocks(); vi.mocked(getMeasurements).mockResolvedValue({ job: null }); });
it("requires actual spacing and explicit metadata save before a run", async () => {
  vi.mocked(editVolume).mockResolvedValue({ ...volume, voxel_size_z: 30, voxel_size_y: 16, voxel_size_x: 16 });
  open();
  await screen.findByText(/No measurements have been run/);
  expect((screen.getByLabelText("Volume") as HTMLSelectElement).value).toBe("4");
  expect((screen.getByLabelText("Z (nm)") as HTMLInputElement).value).toBe("");
  for (const [axis, value] of [["Z", "30"], ["Y", "16"], ["X", "16"]]) {
    fireEvent.change(screen.getByLabelText(`${axis} (nm)`), { target: { value } });
  }
  expect((screen.getByRole("button", { name: "Run measurements" }) as HTMLButtonElement).disabled).toBe(true);
  expect(editVolume).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save voxel size" }));
  await waitFor(() => expect(editVolume).toHaveBeenCalledWith(4, { voxel_size_z: 30, voxel_size_y: 16, voxel_size_x: 16 }));
  await waitFor(() => expect((screen.getByRole("button", { name: "Run measurements" }) as HTMLButtonElement).disabled).toBe(false));
  expect(runMeasurements).not.toHaveBeenCalled();
});
it("does not copy spacing to a different volume", async () => {
  open(); await screen.findByText(/No measurements have been run/);
  fireEvent.change(screen.getByLabelText("Z (nm)"), { target: { value: "30" } });
  fireEvent.change(screen.getByLabelText("Volume"), { target: { value: "3" } });
  expect((screen.getByLabelText("Z (nm)") as HTMLInputElement).value).toBe("");
  await waitFor(() => expect(getMeasurements).toHaveBeenCalledWith(3, "official"));
  expect(editVolume).not.toHaveBeenCalled();
});
it("keeps metadata editing and run controls manager-only", async () => {
  open(false); await screen.findByText(/No measurements have been run/);
  expect(screen.queryByRole("button", { name: "Save voxel size" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Run measurements" })).toBeNull();
});
it("keeps invalid spacing and failed saves from enabling measurement", async () => {
  vi.mocked(editVolume).mockRejectedValue(new Error("Metadata save failed"));
  open(); await screen.findByText(/No measurements have been run/);
  for (const axis of ["Z", "Y", "X"]) fireEvent.change(screen.getByLabelText(`${axis} (nm)`), { target: { value: "0" } });
  expect((screen.getByRole("button", { name: "Save voxel size" }) as HTMLButtonElement).disabled).toBe(true);
  for (const axis of ["Z", "Y", "X"]) fireEvent.change(screen.getByLabelText(`${axis} (nm)`), { target: { value: "16" } });
  fireEvent.click(screen.getByRole("button", { name: "Save voxel size" }));
  expect((await screen.findByRole("alert")).textContent).toBe("Metadata save failed");
  expect((screen.getByRole("button", { name: "Run measurements" }) as HTMLButtonElement).disabled).toBe(true);
});
