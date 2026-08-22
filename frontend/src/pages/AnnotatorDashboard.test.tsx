import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import AnnotatorDashboard from "./AnnotatorDashboard";

const api = vi.hoisted(() => ({
  listMyTasks: vi.fn(),
  listMyCompletedTasks: vi.fn(),
}));
vi.mock("../api/tasks", () => api);
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 7, role: "annotator" }, isManager: false }),
}));

const task = (id: number, volume_name: string) => ({
  id,
  project_title: "Project",
  project: 1,
  volume: id,
  volume_name,
  status: "in_progress",
  priority: 2,
  difficulty: 2,
  z_start: 0,
  z_end: 4,
  task_type: "manual_annotation",
  assigned_to: 7,
  can_annotate: true,
  annotation_locked: false,
  label_type: "none",
});

describe("AnnotatorDashboard", () => {
  it("shows classic My Tasks with only manager-assigned work", async () => {
    api.listMyTasks.mockResolvedValue([task(1, "Assigned volume")]);
    api.listMyCompletedTasks.mockResolvedValue([task(2, "Finished volume")]);

    render(
      <MemoryRouter>
        <AnnotatorDashboard />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "My Tasks" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "My Tasks" }).closest(".role-home")?.classList.contains("role-home-narrow")).toBe(false);
    expect(await screen.findByText("To annotate")).toBeTruthy();
    expect(await screen.findByText("Assigned volume")).toBeTruthy();
    expect(screen.getByRole("table").classList.contains("task-table-annotator")).toBe(true);
    expect(await screen.findByRole("button", { name: "Annotate" })).toBeTruthy();
    expect(screen.queryByText("Finished volume")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /Done/ }));
    expect(await screen.findByText("Finished volume")).toBeTruthy();
    expect(screen.queryByText("Assigned volume")).toBeNull();
  });

  it("shows withdrawn assignments as cancelled history without task actions", async () => {
    api.listMyTasks.mockResolvedValue([]);
    api.listMyCompletedTasks.mockResolvedValue([{
      ...task(3, "Withdrawn volume"),
      history_key: "withdrawal-9",
      status: "cancelled",
      assignment_withdrawn: true,
      withdrawal_reason: "Working team deleted by manager",
      can_annotate: false,
    }]);
    render(<MemoryRouter initialEntries={["/?tab=done"]}><AnnotatorDashboard /></MemoryRouter>);
    expect(await screen.findByText("Withdrawn volume")).toBeTruthy();
    expect(screen.getByText("cancelled")).toBeTruthy();
    expect(screen.getByText("Working team deleted by manager")).toBeTruthy();
    expect(screen.getByText("Withdrawn")).toBeTruthy();
    expect(screen.queryByRole("button", {name: "View"})).toBeNull();
  });

  it("shows transferred assignments as transferred history", async () => {
    api.listMyTasks.mockResolvedValue([]);
    api.listMyCompletedTasks.mockResolvedValue([{
      ...task(4, "Transferred volume"),
      history_key: "withdrawal-10",
      status: "transferred",
      assignment_withdrawn: true,
      assignment_transferred: true,
      withdrawal_reason: "Transferred to another annotator",
      transferred_to_username: "next-user",
      can_annotate: false,
    }]);
    render(<MemoryRouter initialEntries={["/?tab=done"]}><AnnotatorDashboard /></MemoryRouter>);
    expect(await screen.findByText("Transferred volume")).toBeTruthy();
    expect(screen.getByText("transferred")).toBeTruthy();
    expect(screen.getByText("Transferred")).toBeTruthy();
    expect(screen.queryByRole("button", {name: "View"})).toBeNull();
  });
});
