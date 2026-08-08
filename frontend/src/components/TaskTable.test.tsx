import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import TaskTable from "./TaskTable";
import type { AnnotationTask } from "../types/task";

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 7 }, isManager: false }),
}));

describe("TaskTable layout contract", () => {
  it("uses the shared volume columns without duplicate Type or Frames columns", () => {
    const task = {
      id: 1, volume: 2, volume_name: "a-volume-name-that-must-not-push-badges", project_title: "Project",
      has_region_mask: true, region_mask_location: "/region.tif", region_mask_coverage: 0.42, label_type: "prediction",
      file_format: "hdf5", shape_z: 10, shape_y: 20, shape_x: 30,
      voxel_size_z: 0.04, voxel_size_y: 0.008, voxel_size_x: 0.008,
      z_start: 0, z_end: 10, task_type: "prediction_proofreading", status: "assigned",
      assigned_to: 7, assigned_to_username: "ann", can_annotate: true, annotation_locked: false,
    } as AnnotationTask;
    render(<MemoryRouter><TaskTable tasks={[task]} showAssignee={false}/></MemoryRouter>);
    const row = screen.getByText("a-volume-name-that-must-not-push-badges").closest("tr")!;
    const cells = within(row).getAllByRole("cell");
    expect(cells.find(cell => cell.textContent === "42%")).toBeTruthy();
    expect(cells.find(cell => cell.textContent === "prediction")).toBeTruthy();
    expect(cells.find(cell => cell.textContent === "assigned")).toBeTruthy();
    expect(row.querySelector(".task-actions")?.children).toHaveLength(2);
    expect(screen.getByRole("columnheader", { name: "Label type" })).toBeTruthy();
    expect(screen.queryByRole("columnheader", { name: "Type" })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: /Frames/ })).toBeNull();
    expect(screen.getByText("10 × 20 × 30")).toBeTruthy();
    expect(screen.getByText("0.04 × 0.008 × 0.008")).toBeTruthy();
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
      "Volume", "Format", "Shape (Z × Y × X)", "Voxel size (Z × Y × X)",
      "Region coverage", "Label type", "Status", "Details", "View / Annotate",
    ]);
  });
});
