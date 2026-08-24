import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AnnotatorDashboard from "./AnnotatorDashboard";

const api = vi.hoisted(() => ({
  listMyTasks: vi.fn(),
  listMyCompletedTasks: vi.fn(),
}));
const reviewApi = vi.hoisted(() => ({ listReviewLabelComments: vi.fn() }));
vi.mock("../api/tasks", () => api);
vi.mock("../api/reviewLabelComments", () => reviewApi);
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
  beforeEach(() => {
    reviewApi.listReviewLabelComments.mockReset().mockResolvedValue([]);
  });
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

  it("places manager label feedback between To do and Done and opens focused View", async () => {
    api.listMyTasks.mockResolvedValue([]);
    api.listMyCompletedTasks.mockResolvedValue([]);
    reviewApi.listReviewLabelComments.mockResolvedValue([{
      id: 11,
      submission: 9,
      task: 3,
      label_id: 42,
      body: "Separate this contact from its neighbor.",
      author: 1,
      author_username: "manager",
      project_title: "Project",
      volume_name: "Feedback volume",
      view_z: 40,
      view_y: 10,
      view_x: 12,
      view_axis: "z",
      z_start: 0,
      z_end: 4,
      round_number: 1,
      submission_source: "inapp",
      submission_review_status: "revision_requested",
      created_at: "2026-08-24T12:00:00Z",
      updated_at: "2026-08-24T12:00:00Z",
    }]);

    render(<MemoryRouter initialEntries={["/?tab=feedback"]}><AnnotatorDashboard /></MemoryRouter>);
    const tabs = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(tabs[0]).toContain("To do");
    expect(tabs[1]).toContain("Feedback");
    expect(tabs[2]).toContain("Done");
    expect(await screen.findByText("Separate this contact from its neighbor.")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Label #42/ }).getAttribute("href"))
      .toBe("/viewer/tasks/3?feedback=11&submission=9&z=40&y=10&x=12&axis=z&label=42");
    expect(screen.getByRole("button", { name: "Annotate" }).closest("a")?.getAttribute("href"))
      .toBe("/editor/tasks/3?feedback=11&submission=9&z=40&y=10&x=12&axis=z&label=42");
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });
});
