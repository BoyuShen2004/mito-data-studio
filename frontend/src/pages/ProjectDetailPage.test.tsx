import { fireEvent, render, screen, within } from "@testing-library/react";
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
  listProjectTasks: vi.fn(),
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
vi.mock("../api/hardCases", () => ({
  listHardCases: harness.listHardCases,
  setHardCaseStatus: vi.fn(),
}));
vi.mock("../api/statistics", () => ({ getProjectStatistics: vi.fn() }));
vi.mock("../api/tasks", () => ({
  listAnnotators: vi.fn().mockResolvedValue([]),
  listProjectTasks: harness.listProjectTasks,
}));
vi.mock("../api/datasets", () => ({
  deleteProjectForce: vi.fn(),
  projectDependents: vi.fn(),
}));
vi.mock("../components/DatasetsCard", () => ({ default: () => <div>Datasets pane content</div> }));
vi.mock("../components/AssignmentPlanEditor", () => ({ default: () => <div>Assignment editor</div> }));
vi.mock("../components/DeleteButton", () => ({ default: () => <button>Delete</button> }));
vi.mock("../components/ProjectEditForm", () => ({ default: () => <div>Project edit form</div> }));
vi.mock("../components/ShareControl", () => ({
  default: () => <div>Project share control</div>,
  ShareSummary: () => <div>Share summary</div>,
}));

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
    created_by: 1,
    working_team: null,
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

const open = (search: string) =>
  render(
    <MemoryRouter initialEntries={[`/projects/4${search}`]}>
      <Routes><Route path="/projects/:id" element={<ProjectDetailPage />} /></Routes>
    </MemoryRouter>,
  );

describe("ProjectDetailPage tabs", () => {
  beforeEach(() => {
    harness.isManager = true;
    harness.getProjectSummary.mockReset().mockResolvedValue(summary);
    harness.listProjectVolumes.mockReset().mockResolvedValue([]);
    harness.getDeploymentIdentity.mockReset().mockResolvedValue({ features: { FEATURE_DASHBOARDS: false } });
    harness.listHardCases.mockReset().mockResolvedValue([]);
    harness.listProjectMembers.mockReset().mockResolvedValue([]);
    harness.listProjectTasks.mockReset().mockResolvedValue([]);
  });

  it("is a strip of nouns — no Assign, no Activity", async () => {
    open("");
    await screen.findByRole("tab", { name: "Overview" });
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Overview", "Data2", "Tasks2", "Cases0", "People", "Settings",
    ]);
  });

  it("carries a breadcrumb up to the project list", async () => {
    open("");
    await screen.findByRole("tab", { name: "Overview" });
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Projects" }).getAttribute("href"))
      .toBe("/projects");
    expect(within(crumbs).getByText("Project Alpha")).toBeTruthy();
  });

  it("keeps verbs out of the header — Edit, Share and Delete live in Settings", async () => {
    open("");
    await screen.findByRole("tab", { name: "Overview" });
    const header = document.querySelector(".project-header")!;
    expect(header.querySelector("button")).toBeNull();
    expect(screen.queryByText("Project edit form")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Settings" }));
    expect(await screen.findByText("Project edit form")).toBeTruthy();
    expect(screen.getByText("Project share control")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete" }).closest(".danger-zone")).toBeTruthy();
  });

  it("opens a deep link without mounting every project block", async () => {
    open("?tab=tasks");

    expect(await screen.findByRole("button", { name: "Assign volumes" })).toBeTruthy();
    expect(screen.queryByText("Datasets pane content")).toBeNull();
    expect(screen.queryByText("Assignment editor")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /Data/ }));
    expect(await screen.findByText("Datasets pane content")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Assign volumes" })).toBeNull();
  });

  it("reaches the assignment editor as a bulk action inside Tasks, not a tab", async () => {
    open("?tab=tasks");
    fireEvent.click(await screen.findByRole("button", { name: "Assign volumes" }));
    expect(await screen.findByText("Assignment editor")).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /Assign/ })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Back to tasks" }));
    expect(await screen.findByRole("button", { name: "Assign volumes" })).toBeTruthy();
  });

  it("gives requesters the shared nouns but no People or share control", async () => {
    harness.isManager = false;
    open("?tab=people");

    expect((await screen.findByRole("tab", { name: "Overview" })).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByRole("tab", { name: /Assign/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: "People" })).toBeNull();
    expect(screen.getByRole("tab", { name: /Tasks/ })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Cases/ })).toBeTruthy();
    expect(screen.queryByText("Project share control")).toBeNull();
  });

  it("shows a zero-task working-team member honestly under People", async () => {
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
    open("?tab=people");

    expect(await screen.findByText("Howie L.")).toBeTruthy();
    expect(screen.getByText("Working team")).toBeTruthy();
    expect(screen.getByText("No tasks")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Remove membership" })).toBeNull();
  });

  it("puts hard cases under Cases, out of the old Activity drawer", async () => {
    harness.listHardCases.mockResolvedValue([
      {
        id: 12,
        task: 42,
        project: 4,
        project_title: "Project Alpha",
        volume: 2,
        volume_name: "cortex_01",
        label_id: 918,
        category: "",
        note: "left edge is under-segmented",
        z_start: 0,
        z_end: 10,
        status: "open",
        revoked: false,
        created_by_username: "alice",
        created_at: new Date().toISOString(),
        resolved_by_username: "",
        app_url: "/hard-cases/12",
        can_take_down: false,
        message_count: 0,
      },
    ]);
    open("?tab=cases");

    expect(await screen.findByText("left edge is under-segmented")).toBeTruthy();
    expect(screen.getByText("#12")).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Cases\s*1/ })).toBeTruthy();
  });
});
