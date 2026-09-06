import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import WorkList, { type WorkFilter } from "./WorkList";
import type { AnnotationTask } from "../types/task";
import type { HardCase } from "../types/hardCase";
import type { Project } from "../types/project";

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 7 }, isManager: false }),
}));

const DAY = 24 * 60 * 60 * 1000;
const ago = (days: number) => new Date(Date.now() - days * DAY).toISOString();

const task = (over: Partial<AnnotationTask>): AnnotationTask =>
  ({
    id: 42,
    volume: 2,
    volume_name: "cortex_01",
    project_title: "Cortex study",
    dataset: "p10_batch3",
    z_start: 0,
    z_end: 256,
    status: "submitted",
    assigned_to: 7,
    assigned_to_username: "alice",
    can_annotate: true,
    annotation_locked: false,
    instructions: "",
    review_history: [],
    last_decision: "",
    last_decision_at: null,
    last_decision_by_username: "",
    created_at: ago(9),
    assigned_at: ago(8),
    submitted_at: ago(2),
    approved_at: null,
    ...over,
  }) as AnnotationTask;

const hardCase = (over: Partial<HardCase>): HardCase =>
  ({
    id: 12,
    task: 42,
    project: 1,
    project_title: "Cortex study",
    volume: 2,
    volume_name: "cortex_01",
    label_id: 918,
    category: "needs_split",
    note: "left edge is under-segmented",
    z_start: 0,
    z_end: 127,
    status: "open",
    revoked: false,
    created_by_username: "alice",
    created_at: ago(3),
    resolved_by_username: "",
    app_url: "/hard-cases/12",
    can_take_down: true,
    message_count: 2,
    ...over,
  }) as HardCase;

const project = (over: Partial<Project>): Project =>
  ({
    id: 5,
    title: "Cortex study",
    status: "active",
    lifecycle: "new",
    manager_reviewed: true,
    volume_count: 83,
    task_count: 83,
    deadline: "2026-09-12",
    created_by_username: "mgr",
    created_at: ago(20),
    description: "",
    institution_name: "",
    ...over,
  }) as Project;

/** Filters live in the caller's state (the query string in the app), so the
 * tests drive them the same way the pages do. */
function Harness(props: Parameters<typeof WorkList>[0]) {
  const [filter, setFilter] = useState<WorkFilter>({});
  return (
    <MemoryRouter>
      <WorkList {...props} filter={filter} onFilterChange={setFilter} />
    </MemoryRouter>
  );
}

describe("WorkList row anatomy", () => {
  it("gives a task a number, a state with an accessible label, and the latest event", () => {
    render(<Harness kind="task" rows={[task({})]} />);

    expect(screen.getByText("cortex_01 z1–256")).toBeTruthy();
    expect(screen.getByText("#42")).toBeTruthy();
    expect(screen.getByLabelText("State: submitted")).toBeTruthy();
    expect(screen.getByText(/Submitted 2 days ago by alice · awaiting review/)).toBeTruthy();
    expect(screen.getByText("alice", { selector: ".work-row-person" })).toBeTruthy();
  });

  it("prefers the newest event, so a resubmission outranks the decision it answered", () => {
    render(
      <Harness
        kind="task"
        rows={[
          task({
            last_decision: "revision_requested",
            last_decision_at: ago(4),
            last_decision_by_username: "mgr",
            submitted_at: ago(1),
          }),
        ]}
      />,
    );
    expect(screen.getByText(/Submitted 1 day ago by alice/)).toBeTruthy();
  });

  it("shows the decision when it is the newest thing that happened", () => {
    render(
      <Harness
        kind="task"
        rows={[
          task({
            status: "approved",
            last_decision: "approved",
            last_decision_at: ago(1),
            last_decision_by_username: "mgr",
            submitted_at: ago(2),
          }),
        ]}
      />,
    );
    expect(screen.getByText(/Approved 1 day ago by mgr/)).toBeTruthy();
  });

  it("titles a case by its note and carries its category as a chip", () => {
    render(<Harness kind="case" rows={[hardCase({})]} />);

    expect(screen.getByText("left edge is under-segmented")).toBeTruthy();
    expect(screen.getByText("#12")).toBeTruthy();
    expect(screen.getByText("Needs split", { selector: ".work-chip" })).toBeTruthy();
    expect(screen.getByText(/task #42 · raised 3 days ago by alice/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Note (2)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Take down" })).toBeTruthy();
  });

  it("falls back to Label #N when a case has no note", () => {
    render(<Harness kind="case" rows={[hardCase({ note: "" })]} />);
    expect(screen.getByText("Label #918")).toBeTruthy();
  });

  it("renders a project row with its counts", () => {
    render(<Harness kind="project" rows={[project({})]} />);
    expect(screen.getByText("Cortex study")).toBeTruthy();
    expect(screen.getByText(/83 volumes · 83 tasks · deadline 2026-09-12/)).toBeTruthy();
  });
});

describe("WorkList open/closed strip", () => {
  it("counts anything not approved as open, and filters on click", () => {
    render(
      <Harness
        kind="task"
        rows={[
          task({ id: 42, status: "submitted" }),
          task({ id: 43, status: "approved", volume_name: "hippocampus_03" }),
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: /1 Open/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /1 Closed/ })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /1 Open/ }));
    expect(screen.getByText("#42")).toBeTruthy();
    expect(screen.queryByText("#43")).toBeNull();
  });

  it("calls a taken-down case closed, not approved", () => {
    render(
      <Harness
        kind="case"
        rows={[hardCase({ id: 12 }), hardCase({ id: 13, status: "resolved", note: "settled" })]}
      />,
    );
    expect(screen.getByRole("button", { name: /1 Taken down/ })).toBeTruthy();
  });
});

describe("WorkList filtering", () => {
  it("only offers dropdown values that are present in the rows", () => {
    render(
      <Harness
        kind="task"
        rows={[task({ id: 42, assigned_to_username: "alice" }), task({ id: 43, assigned_to_username: "bob" })]}
      />,
    );
    const assignee = screen.getByLabelText("Assignee") as HTMLSelectElement;
    expect(Array.from(assignee.options).map((option) => option.value)).toEqual(["", "alice", "bob"]);
  });

  it("narrows by assignee without a refetch", () => {
    render(
      <Harness
        kind="task"
        rows={[task({ id: 42, assigned_to_username: "alice" }), task({ id: 43, assigned_to_username: "bob" })]}
      />,
    );
    fireEvent.change(screen.getByLabelText("Assignee"), { target: { value: "bob" } });
    expect(screen.queryByText("#42")).toBeNull();
    expect(screen.getByText("#43")).toBeTruthy();
  });

  it("narrows cases by category, keeping uncategorised distinct", () => {
    render(
      <Harness
        kind="case"
        rows={[
          hardCase({ id: 12, category: "needs_split" }),
          hardCase({ id: 13, category: "", note: "no reason given" }),
        ]}
      />,
    );
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "uncategorised" } });
    expect(screen.getByText("#13")).toBeTruthy();
    expect(screen.queryByText("#12")).toBeNull();
  });

  it("searches the text already on the row", () => {
    render(
      <Harness
        kind="task"
        rows={[task({ id: 42, volume_name: "cortex_01" }), task({ id: 43, volume_name: "hippocampus_03" })]}
      />,
    );
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "hippo" } });
    expect(screen.getByText("#43")).toBeTruthy();
    expect(screen.queryByText("#42")).toBeNull();
  });

  it("says the list is empty differently from the filter being empty", () => {
    const { rerender } = render(<Harness kind="task" rows={[]} emptyText="Nothing assigned to you." />);
    expect(screen.getByText("Nothing assigned to you.")).toBeTruthy();

    rerender(<Harness kind="task" rows={[task({})]} emptyText="Nothing assigned to you." />);
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "zzz" } });
    expect(screen.getByText("No rows match this filter.")).toBeTruthy();
  });
});

describe("WorkList task actions", () => {
  it("keeps View and Annotate on the row for the assignee", () => {
    render(<Harness kind="task" rows={[task({ assigned_to: 7, can_annotate: true })]} />);
    const row = screen.getByText("cortex_01 z1–256").closest("li")!;
    expect(within(row).getByRole("button", { name: "View" })).toBeTruthy();
    expect(within(row).getByRole("button", { name: "Annotate" })).toBeTruthy();
  });

  it("drops Annotate for somebody else's task", () => {
    render(<Harness kind="task" rows={[task({ assigned_to: 99, assigned_to_username: "bob" })]} />);
    expect(screen.getByRole("button", { name: "View" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Annotate" })).toBeNull();
  });

  it("keeps a task and its withdrawal record apart, though they share an id", () => {
    // `listMyCompletedTasks` returns both; they differ only by `history_key`.
    // Deriving the row's actions by looking the id back up handed the second
    // row the first one's task.
    render(
      <Harness
        kind="task"
        rows={[
          task({ id: 42, status: "approved", can_annotate: false }),
          task({
            id: 42,
            history_key: "withdrawal-9",
            status: "cancelled",
            assignment_withdrawn: true,
            withdrawal_reason: "reassigned to bob",
            can_annotate: false,
          }),
        ]}
      />,
    );
    expect(screen.getAllByText("#42")).toHaveLength(2);
    expect(screen.getByText("Withdrawn")).toBeTruthy();
    // The live row still offers View; the withdrawal record offers nothing.
    expect(screen.getAllByRole("button", { name: "View" })).toHaveLength(1);
  });

  it("shows a withdrawn assignment as withdrawn instead of offering actions", () => {
    render(
      <Harness
        kind="task"
        rows={[task({ assignment_withdrawn: true, withdrawal_reason: "reassigned to bob" })]}
      />,
    );
    expect(screen.getByText("Withdrawn")).toBeTruthy();
    expect(screen.getByText(/reassigned to bob/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "View" })).toBeNull();
  });
});
