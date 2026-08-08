import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ManagerDashboard from "./ManagerDashboard";

const projectApi = vi.hoisted(() => ({ listProjects: vi.fn() }));
const submissionApi = vi.hoisted(() => ({ listSubmissions: vi.fn() }));
vi.mock("../api/projects", () => projectApi);
vi.mock("../api/submissions", () => submissionApi);
vi.mock("../components/PublicShareTree", () => ({ default: () => <div>Live public shares</div> }));

const project = {
  id: 1,
  title: "Mito project",
  dataset: "Dataset A",
  status: "active",
  manager_reviewed: false,
  created_by_username: "requester1",
  volume_count: 2,
  task_count: 3,
  deadline: null,
};
const submission = {
  id: 9,
  annotator_username: "alice",
  qc_status: "passed",
  submitted_at: "2026-08-04T00:00:00Z",
  task_detail: { volume_name: "volume-a", z_start: 0, z_end: 4 },
};

describe("ManagerDashboard", () => {
  beforeEach(() => {
    projectApi.listProjects.mockReset().mockResolvedValue([project]);
    submissionApi.listSubmissions.mockReset().mockResolvedValue([submission]);
  });

  it("shows one primary panel at a time and switches to shares", async () => {
    render(<MemoryRouter><ManagerDashboard /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Manager Dashboard" })).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeTruthy();
    expect(screen.queryByText("Live public shares")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Reviews" })).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Shares" }));
    expect(await screen.findByText("Live public shares")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Projects" })).toBeNull();
  });

  it("supports a reviews deep link and exposes attention counts", async () => {
    render(<MemoryRouter initialEntries={["/manager?tab=reviews"]}><ManagerDashboard /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Reviews" })).toBeTruthy();
    expect(await screen.findByText("volume-a z1–4")).toBeTruthy();
    expect(screen.getByText("Projects to approve")).toBeTruthy();
    expect(screen.getByText("Submissions to review")).toBeTruthy();
    expect(screen.queryByText("Manager workflow")).toBeNull();
  });

  it("labels approval rows as projects because they open project review", async () => {
    render(<MemoryRouter initialEntries={["/manager?tab=approvals"]}><ManagerDashboard /></MemoryRouter>);
    expect(await screen.findByRole("columnheader", {name: "Project"})).toBeTruthy();
    expect(screen.getByRole("link", {name: "Mito project"}).getAttribute("href")).toBe("/projects/1");
    expect(screen.queryByRole("columnheader", {name: "Dataset"})).toBeNull();
  });
});
