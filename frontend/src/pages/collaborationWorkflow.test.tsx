import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HardCaseDetailPage from "./HardCaseDetailPage";
import HardCaseSharePage from "./HardCaseSharePage";
import ReviewBox from "../components/ReviewBox";
import ReviewSubmissionPage from "./ReviewSubmissionPage";
import { TaskViewerPage } from "./ViewerPage";

const harness = vi.hoisted(() => ({
  asyncData: null as unknown,
  canvasProps: vi.fn(),
  reload: vi.fn(),
  reviewSubmission: vi.fn(),
  submitInappTask: vi.fn(),
  listHardCaseMessages: vi.fn(),
  saveReviewLabelComment: vi.fn(),
  isManager: false,
}));

vi.mock("../hooks/useAsync", () => ({
  useAsync: () => ({
    data: harness.asyncData,
    loading: false,
    error: null,
    reload: harness.reload,
  }),
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 12, username: "alice" },
    isManager: harness.isManager,
  }),
}));

vi.mock("../features/viewer/AnnotationCanvas", () => ({
  default: (props: unknown) => {
    harness.canvasProps(props);
    return <div data-testid="annotation-canvas" />;
  },
}));

vi.mock("../api/submissions", () => ({
  getSubmission: vi.fn(),
  reviewSubmission: harness.reviewSubmission,
  submitInappTask: harness.submitInappTask,
}));

vi.mock("../api/reviewLabelComments", () => ({
  listReviewLabelComments: vi.fn(),
  deleteReviewLabelComment: vi.fn(),
  saveReviewLabelComment: harness.saveReviewLabelComment,
}));

vi.mock("../api/tasks", () => ({
  getTask: vi.fn(),
  listProjectTasks: vi.fn(),
}));

vi.mock("../api/hardCases", () => ({
  getHardCase: vi.fn(),
  setHardCaseRevoked: vi.fn(),
  setHardCaseStatus: vi.fn(),
  listHardCaseMessages: harness.listHardCaseMessages,
  updateHardCaseNote: vi.fn(),
  addHardCaseMessage: vi.fn(),
}));

const task = (overrides: Record<string, unknown> = {}) => ({
  id: 7,
  project: 2,
  project_title: "Project A",
  volume: 9,
  volume_name: "chunk-a",
  assigned_to: 12,
  z_start: 0,
  z_end: 8,
  status: "in_progress",
  annotation_locked: false,
  can_submit: true,
  can_annotate: true,
  submission_count: 0,
  ...overrides,
});

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}{location.search}</div>;
}

describe("collaboration workflow pages", () => {
  beforeEach(() => {
    harness.asyncData = null;
    harness.canvasProps.mockReset();
    harness.reload.mockReset();
    harness.reviewSubmission.mockReset().mockResolvedValue({});
    harness.submitInappTask.mockReset();
    harness.listHardCaseMessages.mockReset().mockResolvedValue([]);
    harness.saveReviewLabelComment.mockReset().mockResolvedValue({});
    harness.isManager = false;
  });

  it("keeps the task canvas mounted and exposes another submit round", async () => {
    const first = task();
    const refreshed = task({ status: "submitted", submission_count: 1 });
    harness.asyncData = first;
    harness.submitInappTask.mockResolvedValue({ task_detail: refreshed });

    render(
      <MemoryRouter initialEntries={["/editor/tasks/7"]}>
        <Routes>
          <Route path="/editor/tasks/:id" element={<TaskViewerPage editable />} />
        </Routes>
      </MemoryRouter>,
    );

    const canvas = screen.getByTestId("annotation-canvas");
    fireEvent.click(screen.getByRole("button", { name: "Submit for review" }));

    await screen.findByRole("button", { name: "Submit again" });
    expect(screen.getByTestId("annotation-canvas")).toBe(canvas);
    expect(harness.submitInappTask).toHaveBeenCalledWith(7);
    expect(harness.canvasProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ editable: true, taskId: 7, volumeId: 9 }),
    );
  });

  it("orders the task identity, volume title, Submit, and Share controls", () => {
    harness.asyncData = task();

    render(
      <MemoryRouter initialEntries={["/editor/tasks/7"]}>
        <Routes>
          <Route path="/editor/tasks/:id" element={<TaskViewerPage editable />} />
        </Routes>
      </MemoryRouter>,
    );

    const heading = screen.getByRole("heading", { name: "Annotate · Task #7" });
    const volumeTitle = screen.getByText("Project A · chunk-a");
    const submit = screen.getByRole("button", { name: "Submit for review" });
    const share = screen.getByRole("button", { name: "Share" });
    const follows = (left: Element, right: Element) =>
      Boolean(left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING);

    expect(follows(heading, volumeTitle)).toBe(true);
    expect(follows(volumeTitle, submit)).toBe(true);
    expect(follows(submit, share)).toBe(true);
  });

  it("centres Region only + Overwrite between Share and Axis, only when the canvas has a Region", () => {
    harness.asyncData = task();

    render(
      <MemoryRouter initialEntries={["/editor/tasks/7"]}>
        <Routes>
          <Route path="/editor/tasks/:id" element={<TaskViewerPage editable />} />
        </Routes>
      </MemoryRouter>,
    );

    const canvasProps = harness.canvasProps.mock.lastCall?.[0] as {
      onAxisControls: (controls: unknown) => void;
    };
    const changeRegionOnly = vi.fn();
    const changeRegionOverwriteMode = vi.fn();
    const controls = {
      axis: "z",
      changeAxis: vi.fn(),
      disabled: false,
      currentLocation: vi.fn(),
      hasRegion: false,
      regionOnly: false,
      changeRegionOnly,
      canMutateLabels: true,
      regionOverwriteMode: "overwrite_empty",
      changeRegionOverwriteMode,
    };

    act(() => canvasProps.onAxisControls(controls));
    expect(screen.queryByRole("button", { name: "Region only" })).toBeNull();
    expect(screen.queryByLabelText("Overwrite")).toBeNull();

    act(() => canvasProps.onAxisControls({ ...controls, hasRegion: true }));
    const axis = screen.getByLabelText("Axis");
    const region = screen.getByRole("button", { name: "Region only" });
    const overwrite = screen.getByLabelText("Overwrite");
    const mode = screen.getByRole("button", { name: "View only" });
    const follows = (left: Element, right: Element) =>
      Boolean(left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING);

    expect(region.getAttribute("aria-pressed")).toBe("false");
    // Overwrite sits immediately right of Region only, and the pair sits left
    // of the Axis/mode cluster — the centre slot, not the actions cluster.
    expect(follows(region, overwrite)).toBe(true);
    expect(follows(overwrite, axis)).toBe(true);
    expect(follows(axis, mode)).toBe(true);
    expect(region.closest(".editor-center-slot")).not.toBeNull();
    expect(region.closest(".editor-actions")).toBeNull();
    expect(axis.closest(".editor-center-slot")).toBeNull();
    // Three sibling slots, so no slot's contents can reach into another's box:
    // Region only is not inside the cluster Share lives in, and Share sits in
    // its own fixed-width reserve inside that cluster.
    const left = document.querySelector(".editor-left-slot") as HTMLElement;
    const centre = document.querySelector(".editor-center-slot") as HTMLElement;
    const right = document.querySelector(".editor-right-slot") as HTMLElement;
    expect(centre.parentElement).toBe(left.parentElement);
    expect(right.parentElement).toBe(left.parentElement);
    expect(left.contains(centre)).toBe(false);
    expect(
      screen.getByRole("button", { name: "Share" }).closest(".editor-share-slot"),
    ).not.toBeNull();
    expect(centre.closest(".editor-share-slot")).toBeNull();

    // Same wording as the Interpolate / Flood fill policy.
    expect(
      [...(overwrite as HTMLSelectElement).options].map((o) => [o.value, o.text]),
    ).toEqual([
      ["overwrite_empty", "Empty voxels only"],
      ["overwrite_all", "All voxels"],
    ]);

    fireEvent.click(region);
    expect(changeRegionOnly).toHaveBeenCalledWith(true);
    fireEvent.change(overwrite, { target: { value: "overwrite_all" } });
    expect(changeRegionOverwriteMode).toHaveBeenCalledWith("overwrite_all");
  });

  it("preserves axis, layer, and Active label when switching Annotate to View", async () => {
    harness.asyncData = task();
    render(
      <MemoryRouter initialEntries={["/editor/tasks/7"]}>
        <Routes>
          <Route path="/editor/tasks/:id" element={<TaskViewerPage editable />} />
          <Route path="/viewer/tasks/:id" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    const canvasProps = harness.canvasProps.mock.lastCall?.[0] as {
      onAxisControls: (controls: unknown) => void;
    };
    act(() => canvasProps.onAxisControls({
      axis: "y",
      changeAxis: vi.fn(),
      disabled: false,
      currentLocation: () => ({ z: 3, y: 5, x: 7, axis: "y", label: 11 }),
      hasRegion: false,
      regionOnly: true,
      changeRegionOnly: vi.fn(),
      canMutateLabels: true,
      regionOverwriteMode: "overwrite_empty",
      changeRegionOverwriteMode: vi.fn(),
    }));

    fireEvent.click(screen.getByRole("button", { name: "View only" }));
    expect((await screen.findByTestId("location")).textContent).toBe(
      "/viewer/tasks/7?z=3&y=5&x=7&axis=y&label=11",
    );
  });

  it("preserves feedback and submission context while switching modes", async () => {
    harness.asyncData = task();
    render(
      <MemoryRouter initialEntries={["/editor/tasks/7?feedback=11&submission=5&z=2&label=6"]}>
        <Routes>
          <Route path="/editor/tasks/:id" element={<TaskViewerPage editable />} />
          <Route path="/viewer/tasks/:id" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    const canvasProps = harness.canvasProps.mock.lastCall?.[0] as {
      onAxisControls: (controls: unknown) => void;
    };
    act(() => canvasProps.onAxisControls({
      axis: "z",
      changeAxis: vi.fn(),
      disabled: false,
      currentLocation: () => ({ z: 2, y: 0, x: 0, axis: "z", label: 6 }),
      hasRegion: false,
      regionOnly: false,
      changeRegionOnly: vi.fn(),
      canMutateLabels: true,
      regionOverwriteMode: "overwrite_empty",
      changeRegionOverwriteMode: vi.fn(),
    }));

    fireEvent.click(screen.getByRole("button", { name: "View only" }));
    expect((await screen.findByTestId("location")).textContent).toBe(
      "/viewer/tasks/7?feedback=11&submission=5&z=2&y=0&x=0&axis=z&label=6",
    );
  });

  it("uses the server lock gates for both painting and submitting", () => {
    harness.asyncData = task({
      status: "approved",
      annotation_locked: true,
      can_submit: false,
      can_annotate: false,
    });

    render(
      <MemoryRouter initialEntries={["/editor/tasks/7"]}>
        <Routes>
          <Route path="/editor/tasks/:id" element={<TaskViewerPage editable />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText(/Approved — closed for further annotation/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Submit/ })).toBeNull();
    expect(harness.canvasProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ editable: false }),
    );
  });

  it("mounts a project hard case with the API-provided edit permission", () => {
    harness.asyncData = {
      id: 3,
      task: 7,
      volume: 9,
      label_id: 44,
      status: "open",
      revoked: false,
      can_annotate: false,
      can_take_down: false,
      can_edit_note: false,
      can_comment: true,
      message_count: 0,
      note: "Needs a second look",
      view_z: null,
      view_y: null,
      view_x: null,
      view_axis: "",
      z_start: 2,
      z_end: 6,
      project_title: "Project A",
      volume_name: "chunk-a",
      created_by_username: "bob",
      created_at: "2026-07-30T12:00:00Z",
      url: "/share/hard-case/token",
    };

    render(
      <MemoryRouter initialEntries={["/hard-cases/3"]}>
        <Routes>
          <Route path="/hard-cases/:id" element={<HardCaseDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    // The issue skeleton: title, #id, state, and the note as the page's own
    // content rather than something behind a dialog.
    expect(screen.getByRole("heading", { name: "Needs a second look" })).toBeTruthy();
    expect(screen.getByText("#3")).toBeTruthy();
    expect(screen.getByText("open")).toBeTruthy();
    expect(screen.getByText(/view only/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Discussion" })).toBeTruthy();
    // can_edit_note is false, so the note is read, not edited.
    expect(screen.queryByRole("textbox", { name: "Primary note" })).toBeNull();
    // can_comment is true, so the reply box is there, at the end.
    expect(screen.getByRole("textbox", { name: "Discussion reply" })).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(harness.canvasProps).toHaveBeenLastCalledWith(
      expect.objectContaining({
        taskId: 7,
        volumeId: 9,
        editable: false,
        initialActiveId: 44,
        initialSoloId: 44,
      }),
    );
  });

  it("solos the same shared label on the public, no-account share page", () => {
    harness.asyncData = {
      task_id: 7,
      volume_id: 9,
      label_id: 44,
      z_start: 2,
      z_end: 6,
      project_title: "Project A",
      volume_name: "chunk-a",
    };

    render(
      <MemoryRouter initialEntries={["/share/hard-case/token"]}>
        <Routes>
          <Route path="/share/hard-case/:token" element={<HardCaseSharePage />} />
        </Routes>
      </MemoryRouter>,
    );

    // Both hard-case entry points hand the canvas the same focus props, so the
    // canvas's "jump to the shared label's layer" applies to each of them —
    // see `AnnotationCanvasHardCaseFocus.test.tsx`.
    expect(harness.canvasProps).toHaveBeenLastCalledWith(
      expect.objectContaining({
        taskId: 7,
        volumeId: 9,
        zStart: 2,
        editable: false,
        initialActiveId: 44,
        initialSoloId: 44,
      }),
    );
  });

  const submission = (overrides: Record<string, unknown> = {}) => ({
    id: 5,
    task: 7,
    task_detail: task({ submission_count: 1 }),
    annotator_username: "alice",
    source: "inapp",
    round_number: 1,
    submitted_at: "2026-07-30T12:00:00Z",
    label_file: "",
    notes: "",
    qc_status: "passed",
    qc_report: {},
    reviews: [],
    project_title: "Project A",
    volume_name: "chunk-a",
    ...overrides,
  });

  it("sends the approve-time keep-open decision explicitly", async () => {
    harness.asyncData = submission();

    render(
      <MemoryRouter>
        <ReviewBox submissionId={5} onDecided={harness.reload} />
      </MemoryRouter>,
    );

    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /Allow further annotation after approval/,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve & keep open" }));

    await waitFor(() =>
      expect(harness.reviewSubmission).toHaveBeenCalledWith(5, "approved", "", true),
    );
  });

  it("leaves the reviewer on the task instead of redirecting to a role home", async () => {
    harness.asyncData = submission();

    render(
      <MemoryRouter>
        <ReviewBox submissionId={5} onDecided={harness.reload} nextHref="/tasks/8" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Approve & close" }));

    // The decision is recorded in place and the task is asked to refresh, so
    // the new state and the new timeline entry appear where the work happened.
    await waitFor(() => expect(harness.reload).toHaveBeenCalled());
    expect(screen.getByText(/recorded/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Next waiting submission/ }).getAttribute("href"))
      .toBe("/tasks/8");
  });

  it("offers view-only and annotate routes before an in-app review decision", () => {
    harness.asyncData = submission();

    render(
      <MemoryRouter>
        <ReviewBox submissionId={5} onDecided={harness.reload} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "Review this submission" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "View this submission" }).closest("a")?.getAttribute("href"))
      .toBe("/viewer/tasks/7?submission=5");
    expect(screen.getByRole("button", { name: "Annotate" }).closest("a")?.getAttribute("href"))
      .toBe("/editor/tasks/7");
    expect(screen.getByRole("heading", { name: "Commented instances" })).toBeTruthy();
  });

  it("keeps /submissions/:id/review working, as a redirect onto the task", async () => {
    harness.asyncData = submission();

    render(
      <MemoryRouter initialEntries={["/submissions/5/review"]}>
        <Routes>
          <Route path="/submissions/:id/review" element={<ReviewSubmissionPage />} />
          <Route path="/tasks/:id" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect((await screen.findByTestId("location")).textContent).toBe("/tasks/7");
  });

  it("lets a manager save a label comment only from submission-aware View", async () => {
    harness.isManager = true;
    harness.asyncData = task();
    render(
      <MemoryRouter initialEntries={["/viewer/tasks/7?submission=5"]}>
        <Routes>
          <Route path="/viewer/tasks/:id" element={<TaskViewerPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const props = harness.canvasProps.mock.lastCall?.[0] as {
      onCommentLabel?: (labelId: number) => void;
    };
    expect(props.onCommentLabel).toBeTypeOf("function");
    act(() => props.onCommentLabel?.(6));
    fireEvent.change(screen.getByRole("textbox", { name: "Manager feedback" }), {
      target: { value: "Reconnect this narrow branch." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save comment" }));
    await waitFor(() => expect(harness.saveReviewLabelComment)
      .toHaveBeenCalledWith(
        5,
        7,
        6,
        "Reconnect this narrow branch.",
        { z: 0, y: 0, x: 0, axis: "z", label: 6 },
      ));
  });
});
