import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProjectDetailPage from "./ProjectDetailPage";

const harness = vi.hoisted(() => ({
  isManager: true,
  getProjectSummary: vi.fn(),
  listProjectVolumes: vi.fn(),
  getDeploymentIdentity: vi.fn(),
  listHardCases: vi.fn(),
  listProjectMembers: vi.fn(),
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ isManager: harness.isManager }),
}));
vi.mock("../api/projects", () => ({
  getProjectSummary: harness.getProjectSummary,
  reviewProject: vi.fn(),
  listProjectMembers: harness.listProjectMembers,
  addProjectMember: vi.fn(),
  removeProjectMember: vi.fn(),
}));
vi.mock("../api/volumes", () => ({ listProjectVolumes: harness.listProjectVolumes }));
vi.mock("../api/deployment", () => ({ getDeploymentIdentity: harness.getDeploymentIdentity }));
vi.mock("../api/hardCases", () => ({ listHardCases: harness.listHardCases }));
vi.mock("../api/statistics", () => ({ getProjectStatistics: vi.fn() }));
vi.mock("../api/tasks", () => ({ listAnnotators: vi.fn().mockResolvedValue([]) }));
vi.mock("../api/datasets", () => ({
  deleteProjectForce: vi.fn(),
  projectDependents: vi.fn(),
}));
vi.mock("../components/DatasetsCard", () => ({ default: () => <div>Datasets pane content</div> }));
vi.mock("../components/AssignmentPlanEditor", () => ({ default: () => <div>Assignment editor</div> }));
vi.mock("../components/DeleteButton", () => ({ default: () => <button>Delete</button> }));
vi.mock("../components/ProjectEditForm", () => ({ default: () => <div>Project edit form</div> }));
vi.mock("../components/ShareControl", () => ({ default: () => <div>Project share control</div> }));
vi.mock("../components/HardCaseList", () => ({ default: () => <div>Hard cases list</div> }));

const summary = {
  project: {
    id: 4,
    title: "Project Alpha",
    dataset_count: 1,
    datasets: [],
    annotation_type: "instance_segmentation",
    annotation_target: "mitochondria",
    deadline: null,
    status: "active",
    manager_reviewed: true,
    volume_count: 2,
    task_count: 2,
  },
  progress: {
    volumes: 2,
    total_tasks: 2,
    approved_tasks: 1,
    percent_complete: 50,
    status_counts: {},
  },
  workload: [],
};

describe("ProjectDetailPage tabs", () => {
  beforeEach(() => {
    harness.isManager = true;
    harness.getProjectSummary.mockReset().mockResolvedValue(summary);
    harness.listProjectVolumes.mockReset().mockResolvedValue([]);
    harness.getDeploymentIdentity.mockReset().mockResolvedValue({ features: { FEATURE_DASHBOARDS: false } });
    harness.listHardCases.mockReset().mockResolvedValue([]);
    harness.listProjectMembers.mockReset().mockResolvedValue([]);
  });

  it("opens a manager deep link without mounting every project block", async () => {
    render(
      <MemoryRouter initialEntries={["/projects/4?tab=assign"]}>
        <Routes><Route path="/projects/:id" element={<ProjectDetailPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Assignment editor")).toBeTruthy();
    expect(screen.queryByText("Datasets pane content")).toBeNull();
    expect(screen.queryByText("Hard cases list")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /Data/ }));
    expect(await screen.findByText("Datasets pane content")).toBeTruthy();
    expect(screen.queryByText("Assignment editor")).toBeNull();
  });

  it("gives requesters only overview, data, and activity panes", async () => {
    harness.isManager = false;
    render(
      <MemoryRouter initialEntries={["/projects/4?tab=access"]}>
        <Routes><Route path="/projects/:id" element={<ProjectDetailPage />} /></Routes>
      </MemoryRouter>,
    );

    expect((await screen.findByRole("tab", { name: "Overview" })).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByRole("tab", { name: /Assign/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Access" })).toBeNull();
    expect(screen.queryByText("Project share control")).toBeNull();
  });

  it("shows a zero-task working-team member honestly in Access", async () => {
    harness.listProjectMembers.mockResolvedValue([{
      user_id: 12,
      username: "howie",
      display_name: "Howie L.",
      is_explicit: false,
      is_working_team: true,
      has_tasks: false,
      access_reason: "Working team",
      membership_id: null,
      created_at: null,
    }]);
    render(
      <MemoryRouter initialEntries={["/projects/4?tab=access"]}>
        <Routes><Route path="/projects/:id" element={<ProjectDetailPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Howie L.")).toBeTruthy();
    expect(screen.getByText("Working team")).toBeTruthy();
    expect(screen.getByText("No tasks")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Remove membership" })).toBeNull();
  });
});
