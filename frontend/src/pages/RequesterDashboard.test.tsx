import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import RequesterDashboard from "./RequesterDashboard";

vi.mock("../api/projects", () => ({
  listProjects: vi.fn().mockResolvedValue([{
    id: 3,
    title: "Requester project",
    dataset: "Requester dataset",
    lifecycle: "active",
    status: "active",
    manager_reviewed: false,
    volume_count: 4,
    task_count: 0,
    created_at: "2026-08-04T00:00:00Z",
  }]),
}));
vi.mock("../features/lifecycle/api", () => ({ getLifecycleCounts: vi.fn().mockResolvedValue({}) }));
vi.mock("../features/lifecycle/LifecycleTabs", () => ({ default: () => <div>Lifecycle filters</div> }));

describe("RequesterDashboard", () => {
  it("centres projects and registration without manager operations", async () => {
    render(<MemoryRouter><RequesterDashboard /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "My Projects" })).toBeTruthy();
    expect(await screen.findByText("Requester project")).toBeTruthy();
    expect(screen.getByRole("columnheader", {name: "Project"})).toBeTruthy();
    expect(screen.getByRole("link", {name: "Requester project"}).getAttribute("href")).toBe("/projects/3");
    expect(screen.getByText("Add data")).toBeTruthy();
    expect(screen.queryByText("Live public shares")).toBeNull();
    expect(screen.queryByText("Submissions to review")).toBeNull();
  });
});
