import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskDetailPage from "./TaskDetailPage";
import type { AnnotationTask } from "../types/task";

const harness = vi.hoisted(() => ({
  userId: 7,
  isManager: false,
  getTask: vi.fn(),
  listHardCases: vi.fn(),
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: harness.userId }, isManager: harness.isManager }),
}));
vi.mock("../api/tasks", () => ({ getTask: harness.getTask }));
vi.mock("../api/submissions", () => ({ listSubmissions: vi.fn().mockResolvedValue([]) }));
vi.mock("../api/hardCases", () => ({ listHardCases: harness.listHardCases }));
vi.mock("../components/ReviewBox", () => ({
  default: ({ submissionId }: { submissionId: number }) => (
    <div>Review box for submission {submissionId}</div>
  ),
}));

type Round = AnnotationTask["review_history"][number];

const round = (over: Partial<Round> = {}): Round => ({
  id: 100,
  round_number: 1,
  annotator_username: "alice",
  submitted_at: "2026-09-04T11:00:00Z",
  superseded_at: null,
  superseded_reason: "",
  source: "inapp" as const,
  review_status: "pending" as const,
  reviews: [],
  ...over,
});

const task = (over: Partial<AnnotationTask> = {}): AnnotationTask =>
  ({
    id: 42,
    project: 3,
    project_title: "Cortex study",
    dataset: "p10_batch3",
    volume: 9,
    volume_name: "cortex_01",
    z_start: 0,
    z_end: 256,
    status: "submitted",
    task_type: "manual_annotation",
    priority: 4,
    difficulty: 3,
    deadline: "2026-09-12",
    instructions: "Trace every mitochondrion.",
    label_type: "prediction",
    assigned_to: 7,
    assigned_to_username: "alice",
    can_annotate: true,
    can_submit: true,
    annotation_locked: false,
    submission_count: 1,
    annotation_time: { tracked: true, seconds: 8040, display: "2h 14m" },
    created_at: "2026-09-03T08:00:00Z",
    assigned_at: "2026-09-03T09:00:00Z",
    submitted_at: "2026-09-04T11:00:00Z",
    approved_at: null,
    last_decision: "",
    last_decision_at: null,
    last_decision_by_username: "",
    review_history: [round()],
    ...over,
  }) as AnnotationTask;

const open = (search = "") =>
  render(
    <MemoryRouter initialEntries={[`/tasks/42${search}`]}>
      <Routes><Route path="/tasks/:id" element={<TaskDetailPage />} /></Routes>
    </MemoryRouter>,
  );

describe("TaskDetailPage", () => {
  beforeEach(() => {
    harness.userId = 7;
    harness.isManager = false;
    harness.getTask.mockReset().mockResolvedValue(task());
    harness.listHardCases.mockReset().mockResolvedValue([]);
  });

  it("is titled by its volume and addressed by #id, with a breadcrumb to its project", async () => {
    open();
    expect(await screen.findByRole("heading", { name: "cortex_01 z1–256" })).toBeTruthy();
    expect(screen.getByText("#42")).toBeTruthy();

    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Cortex study" }).getAttribute("href"))
      .toBe("/projects/3");
    expect(within(crumbs).getByRole("link", { name: "Tasks" }).getAttribute("href"))
      .toBe("/projects/3?tab=tasks");
    expect(within(crumbs).getByText("cortex_01 z1–256 #42")).toBeTruthy();
  });

  it("opens the same page for a manager as for an annotator — no redirect", async () => {
    harness.isManager = true;
    harness.userId = 99;
    open();
    expect(await screen.findByRole("heading", { name: "cortex_01 z1–256" })).toBeTruthy();
  });

  it("keeps View and Annotate the most prominent control in the header", async () => {
    open();
    await screen.findByRole("heading", { name: "cortex_01 z1–256" });
    const header = document.querySelector(".task-header")!;
    expect(within(header as HTMLElement).getByRole("button", { name: "View" }).closest("a")?.getAttribute("href"))
      .toBe("/viewer/tasks/42");
    expect(within(header as HTMLElement).getByRole("button", { name: "Annotate" }).closest("a")?.getAttribute("href"))
      .toBe("/editor/tasks/42");
  });

  it("is a timeline, not a stack of equal-weight cards", async () => {
    harness.getTask.mockResolvedValue(
      task({
        status: "revision_requested",
        last_decision: "revision_requested",
        last_decision_at: "2026-09-04T15:00:00Z",
        last_decision_by_username: "mgr",
        review_history: [
          round({
            review_status: "revision_requested",
            reviews: [
              {
                id: 200,
                decision: "revision_requested",
                source: "inapp",
                comments: "left edge is under-segmented",
                reviewer_username: "mgr",
                reviewed_at: "2026-09-04T15:00:00Z",
              },
            ],
          }),
        ],
      }),
    );
    open();

    const timeline = await screen.findByRole("list");
    const entries = within(timeline).getAllByRole("listitem").map((li) => li.textContent);
    expect(entries[0]).toContain("Task opened");
    expect(entries[1]).toContain("alice was assigned");
    expect(entries[2]).toContain("alice submitted round 1 (in-app)");
    expect(entries[3]).toContain("mgr requested changes");
    expect(screen.getByText("left edge is under-segmented")).toBeTruthy();
  });

  it("puts the metadata in a sidebar, never in the narrative", async () => {
    open();
    await screen.findByRole("heading", { name: "cortex_01 z1–256" });
    const sidebar = document.querySelector(".task-sidebar") as HTMLElement;
    expect(within(sidebar).getByText("High")).toBeTruthy();
    expect(within(sidebar).getByText("Moderate")).toBeTruthy();
    expect(within(sidebar).getByText("2026-09-12")).toBeTruthy();
    expect(within(sidebar).getByText("Trace every mitochondrion.")).toBeTruthy();
    expect(within(sidebar).getByText("2h 14m")).toBeTruthy();
  });

  it("shows — for unmeasured annotation time, never a fabricated zero", async () => {
    harness.getTask.mockResolvedValue(
      task({ annotation_time: { tracked: false, seconds: null, display: "—" } }),
    );
    open();
    await screen.findByRole("heading", { name: "cortex_01 z1–256" });
    expect(screen.getByText("—", { selector: ".annotation-time-unknown" })).toBeTruthy();
    expect(screen.queryByText("0m")).toBeNull();
  });

  it("gives a manager the review box at the end of the timeline", async () => {
    harness.isManager = true;
    open();
    expect(await screen.findByText("Review box for submission 100")).toBeTruthy();
  });

  it("tells a manager there is nothing to decide rather than showing a dead form", async () => {
    harness.isManager = true;
    harness.getTask.mockResolvedValue(
      task({ status: "in_progress", review_history: [], submitted_at: null }),
    );
    open();
    expect(await screen.findByText(/Nothing is waiting on you here/)).toBeTruthy();
    expect(screen.queryByText(/Review box/)).toBeNull();
  });

  it("gives the assignee their own action box, with Save and Submit kept distinct", async () => {
    open();
    expect(await screen.findByRole("heading", { name: "Your turn" })).toBeTruthy();
    expect(screen.getByText(/Save as you go; Submit takes the snapshot/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Submit a label file/ }).closest("a")?.getAttribute("href"))
      .toBe("/tasks/42/submit");
  });

  it("shows a bystander the state, not a disabled button to reason about", async () => {
    harness.userId = 99;
    open();
    expect(await screen.findByText(/This task is assigned to alice/)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Your turn" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Annotate" })).toBeNull();
  });

  it("keeps the canvas a sibling tab, so detail has somewhere to go that is not further down", async () => {
    open();
    await screen.findByRole("heading", { name: "cortex_01 z1–256" });
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Conversation3", "Labels", "History1",
    ]);

    fireEvent.click(screen.getByRole("tab", { name: /Labels/ }));
    expect(await screen.findByRole("heading", { name: "Labels" })).toBeTruthy();
    expect(document.querySelector(".task-timeline")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /History/ }));
    expect(await screen.findByRole("heading", { name: "Submission rounds" })).toBeTruthy();
  });

  it("links the hard cases raised on this volume from the sidebar", async () => {
    harness.listHardCases.mockResolvedValue([
      { id: 12, app_url: "/hard-cases/12", status: "open" },
      { id: 15, app_url: "/hard-cases/15", status: "resolved" },
    ]);
    open();
    await screen.findByRole("heading", { name: "cortex_01 z1–256" });
    const sidebar = document.querySelector(".task-sidebar") as HTMLElement;
    expect(within(sidebar).getByRole("link", { name: "#12" }).getAttribute("href"))
      .toBe("/hard-cases/12");
    expect(within(sidebar).getByRole("link", { name: "#15" }).getAttribute("href"))
      .toBe("/hard-cases/15");
  });
});
