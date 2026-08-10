import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HardCaseDetailPage from "./HardCaseDetailPage";
import HardCaseSharePage from "./HardCaseSharePage";
import ReviewSubmissionPage from "./ReviewSubmissionPage";
import { TaskViewerPage } from "./ViewerPage";

const harness = vi.hoisted(() => ({
  asyncData: null as unknown,
  canvasProps: vi.fn(),
  reload: vi.fn(),
  reviewSubmission: vi.fn(),
  submitInappTask: vi.fn(),
  listHardCaseMessages: vi.fn(),
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
    isManager: false,
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

describe("collaboration workflow pages", () => {
  beforeEach(() => {
    harness.asyncData = null;
    harness.canvasProps.mockReset();
    harness.reload.mockReset();
    harness.reviewSubmission.mockReset().mockResolvedValue({});
    harness.submitInappTask.mockReset();
    harness.listHardCaseMessages.mockReset().mockResolvedValue([]);
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

    expect(screen.getByText("View only")).toBeTruthy();
    expect(screen.queryByText(/Note: Needs a second look/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Note" }));
    expect(screen.getByRole("dialog", { name: "Notes · label #44" })).toBeTruthy();
    expect(screen.getByText("Needs a second look")).toBeTruthy();
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

  it("sends the approve-time keep-open decision explicitly", async () => {
    harness.asyncData = {
      id: 5,
      task: 7,
      task_detail: task({ submission_count: 1 }),
      annotator_username: "alice",
      source: "inapp",
      submitted_at: "2026-07-30T12:00:00Z",
      label_file: "",
      notes: "",
      qc_status: "passed",
      qc_report: {},
      reviews: [],
      project_title: "Project A",
      volume_name: "chunk-a",
    };

    render(
      <MemoryRouter initialEntries={["/submissions/5/review"]}>
        <Routes>
          <Route path="/submissions/:id/review" element={<ReviewSubmissionPage />} />
          <Route path="/manager" element={<div>manager home</div>} />
        </Routes>
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
});
