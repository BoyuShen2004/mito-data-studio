import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "./HomePage";

const harness = vi.hoisted(() => ({
  role: "manager" as "manager" | "requester" | "annotator",
  listProjects: vi.fn(),
  listSubmissions: vi.fn(),
  listMyTasks: vi.fn(),
  listMyCompletedTasks: vi.fn(),
  listHardCases: vi.fn(),
  listReviewLabelComments: vi.fn(),
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 7, role: harness.role },
    isManager: harness.role === "manager",
    isRequester: harness.role === "requester",
  }),
}));
vi.mock("../api/projects", () => ({ listProjects: harness.listProjects }));
vi.mock("../api/submissions", () => ({ listSubmissions: harness.listSubmissions }));
vi.mock("../api/tasks", () => ({
  listMyTasks: harness.listMyTasks,
  listMyCompletedTasks: harness.listMyCompletedTasks,
}));
vi.mock("../api/hardCases", () => ({
  listHardCases: harness.listHardCases,
  setHardCaseStatus: vi.fn(),
}));
vi.mock("../api/reviewLabelComments", () => ({
  listReviewLabelComments: harness.listReviewLabelComments,
}));
vi.mock("../components/PublicShareTree", () => ({ default: () => <div>Live public shares</div> }));

const project = {
  id: 1,
  title: "Mito project",
  status: "active",
  lifecycle: "new",
  manager_reviewed: false,
  created_by_username: "requester1",
  volume_count: 2,
  task_count: 3,
  deadline: null,
  created_at: "2026-08-04T00:00:00Z",
  description: "",
  institution_name: "",
};

const task = (id: number, volume_name: string, over: Record<string, unknown> = {}) => ({
  id,
  project: 1,
  project_title: "Project",
  volume: id,
  volume_name,
  status: "in_progress",
  z_start: 0,
  z_end: 4,
  assigned_to: 7,
  assigned_to_username: "alice",
  can_annotate: true,
  annotation_locked: false,
  label_type: "none",
  review_history: [],
  last_decision: "",
  last_decision_at: null,
  last_decision_by_username: "",
  created_at: "2026-08-04T00:00:00Z",
  assigned_at: "2026-08-05T00:00:00Z",
  submitted_at: null,
  approved_at: null,
  ...over,
});

const open = (search = "") =>
  render(
    <MemoryRouter initialEntries={[`/${search}`]}>
      <HomePage />
    </MemoryRouter>,
  );

describe("HomePage", () => {
  beforeEach(() => {
    harness.role = "manager";
    harness.listProjects.mockReset().mockResolvedValue([project]);
    harness.listSubmissions.mockReset().mockResolvedValue([]);
    harness.listMyTasks.mockReset().mockResolvedValue([]);
    harness.listMyCompletedTasks.mockReset().mockResolvedValue([]);
    harness.listHardCases.mockReset().mockResolvedValue([]);
    harness.listReviewLabelComments.mockReset().mockResolvedValue([]);
  });

  it("gives a manager their four queues plus shares, and no attention rail", async () => {
    open();
    await screen.findByRole("heading", { name: "Home" });
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Awaiting review0", "Projects to approve1", "Assigned to me0", "Open cases0", "Shares",
    ]);
    // The rail duplicated two of its own tabs as links; the counts are the signal.
    expect(document.querySelector(".attention-rail")).toBeNull();
  });

  it("renders a waiting submission as its task row, not a submission row", async () => {
    harness.listSubmissions.mockResolvedValue([
      {
        id: 9,
        task: 30,
        source: "inapp",
        annotator_username: "alice",
        qc_status: "passed",
        submitted_at: "2026-08-06T00:00:00Z",
        label_comment_count: 2,
        task_detail: task(30, "volume-a", {
          status: "submitted",
          submitted_at: "2026-08-06T00:00:00Z",
        }),
      },
    ]);
    open();

    expect(await screen.findByText("volume-a z1–4")).toBeTruthy();
    expect(screen.getByText("#30")).toBeTruthy();
    // The address is the task's, never the submission's.
    expect(screen.queryByText("#9")).toBeNull();
    expect(screen.getByRole("tab", { name: /Awaiting review\s*1/ })).toBeTruthy();
  });

  it("switches to shares without leaving a second panel mounted", async () => {
    open();
    await screen.findByRole("heading", { name: "Home" });
    expect(screen.queryByText("Live public shares")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Shares" }));
    expect(await screen.findByText("Live public shares")).toBeTruthy();
  });

  it("links an approval row to the project it opens", async () => {
    open("?tab=approve");
    expect((await screen.findByRole("link", { name: "Mito project" })).getAttribute("href"))
      .toBe("/projects/1");
    expect(screen.getByText("awaiting approval")).toBeTruthy();
  });

  it("gives an annotator their own queues and keeps Annotate on the row", async () => {
    harness.role = "annotator";
    harness.listMyTasks.mockResolvedValue([task(1, "Assigned volume")]);
    harness.listMyCompletedTasks.mockResolvedValue([task(2, "Finished volume", { status: "approved" })]);
    open();

    expect(await screen.findByText("Assigned volume z1–4")).toBeTruthy();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Assigned to me1", "Needs revision0", "Done1", "Feedback0", "Cases in my projects0",
    ]);
    expect(screen.getByRole("button", { name: "Annotate" })).toBeTruthy();
    expect(screen.queryByText("Finished volume z1–4")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /Done/ }));
    expect(await screen.findByText("Finished volume z1–4")).toBeTruthy();
    expect(screen.queryByText("Assigned volume z1–4")).toBeNull();
  });

  it("puts a handed-back task in Needs revision without a second endpoint", async () => {
    harness.role = "annotator";
    harness.listMyTasks.mockResolvedValue([
      task(1, "Assigned volume"),
      task(5, "Returned volume", {
        status: "revision_requested",
        last_decision: "revision_requested",
        last_decision_at: "2026-08-07T00:00:00Z",
        last_decision_by_username: "mgr",
      }),
    ]);
    open("?tab=revision");

    expect(await screen.findByText("Returned volume z1–4")).toBeTruthy();
    expect(screen.queryByText("Assigned volume z1–4")).toBeNull();
    expect(screen.getByText(/Revision requested .* by mgr/)).toBeTruthy();
    expect(harness.listMyTasks).toHaveBeenCalledTimes(1);
  });

  it("shows a withdrawn assignment as history with no task actions", async () => {
    harness.role = "annotator";
    harness.listMyCompletedTasks.mockResolvedValue([
      task(3, "Withdrawn volume", {
        history_key: "withdrawal-9",
        status: "cancelled",
        assignment_withdrawn: true,
        withdrawal_reason: "Working team deleted by manager",
        can_annotate: false,
      }),
    ]);
    open("?tab=done");

    expect(await screen.findByText("Withdrawn volume z1–4")).toBeTruthy();
    expect(screen.getByLabelText("State: cancelled")).toBeTruthy();
    expect(screen.getByText(/Working team deleted by manager/)).toBeTruthy();
    expect(screen.getByText("Withdrawn")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "View" })).toBeNull();
  });

  it("shows a transferred assignment as transferred history", async () => {
    harness.role = "annotator";
    harness.listMyCompletedTasks.mockResolvedValue([
      task(4, "Transferred volume", {
        history_key: "withdrawal-10",
        status: "transferred",
        assignment_withdrawn: true,
        assignment_transferred: true,
        withdrawal_reason: "Transferred to another annotator",
        can_annotate: false,
      }),
    ]);
    open("?tab=done");

    expect(await screen.findByText("Transferred volume z1–4")).toBeTruthy();
    expect(screen.getByLabelText("State: transferred")).toBeTruthy();
    expect(screen.getByText("Transferred")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "View" })).toBeNull();
  });

  it("keeps manager label feedback reachable, with its focused View link", async () => {
    harness.role = "annotator";
    harness.listReviewLabelComments.mockResolvedValue([
      {
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
      },
    ]);
    open("?tab=feedback");

    expect(await screen.findByText("Separate this contact from its neighbor.")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Label #42/ }).getAttribute("href"))
      .toBe("/viewer/tasks/3?feedback=11&submission=9&z=40&y=10&x=12&axis=z&label=42");
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("gives a requester one tab and none of the manager queues", async () => {
    harness.role = "requester";
    open();

    expect(await screen.findByText("Mito project")).toBeTruthy();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["My projects1"]);
    expect(screen.getByRole("link", { name: "Mito project" }).getAttribute("href")).toBe("/projects/1");
    expect(screen.queryByText("Live public shares")).toBeNull();
    expect(screen.queryByRole("tab", { name: /Awaiting review/ })).toBeNull();
    // They watch; they do not work a queue.
    expect(harness.listMyTasks).not.toHaveBeenCalled();
    expect(harness.listSubmissions).not.toHaveBeenCalled();
  });
});
