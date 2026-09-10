import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AnnotationCanvas from "./AnnotationCanvas";

/**
 * Picking a label with the **Select** tool must behave like clicking its row in
 * the Labels list: set Active only. Auto-jumping to ``z_start`` was yanking
 * managers/annotators off the layer they were reviewing.
 */

vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: { id: 4 } }) }));

vi.mock("../rendering", () => ({
  phase14ChunkRendererEnabled: () => false,
  ChunkRenderedImageSource: class {},
}));

vi.mock("./Labels3DPanel", () => ({ default: () => <div /> }));

const hoisted = vi.hoisted(() => ({
  fetchObjectUrl: vi.fn(),
  putLabelIds: vi.fn(),
  setLabelLifecycle: vi.fn(),
  createHardCase: vi.fn(),
  listHardCaseMessages: vi.fn(),
  updateHardCaseNote: vi.fn(),
  addHardCaseMessage: vi.fn(),
  getTrackingPrompts: vi.fn(),
  trackTaskBatch: vi.fn(),
}));

vi.mock("../../api/hardCases", () => ({
  createHardCase: hoisted.createHardCase,
  listHardCaseMessages: hoisted.listHardCaseMessages,
  updateHardCaseNote: hoisted.updateHardCaseNote,
  addHardCaseMessage: hoisted.addHardCaseMessage,
}));

vi.mock("../../api/viewer", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/viewer")>()),
  fetchObjectUrl: hoisted.fetchObjectUrl,
  putLabelIds: hoisted.putLabelIds,
  setLabelLifecycle: hoisted.setLabelLifecycle,
  getTrackingPrompts: hoisted.getTrackingPrompts,
  trackTaskBatch: hoisted.trackTaskBatch,
}));

const meta = {
  shape: { z: 8, y: 4, x: 4 },
  dtype: "uint8",
  axes: ["z", "y", "x"],
  has_label: true,
  has_region_mask: false,
  volume_id: 3,
  ready_streaming: false,
  region_ready_streaming: false,
  display_range: { lo: 0, hi: 255 },
};

/** Label 6 fills the plane the viewer opens on, but the summary says it starts
 * at z=4 — so a jump is observable and a no-jump is too. */
const plane = { shape: [4, 4] as [number, number], runs: [[6, 16]] as [number, number][] };

const api = {
  getVolumeMeta: vi.fn(async () => meta),
  getLabelState: vi.fn(async () => ({ max_label_id: 6, next_label_id: 7 })),
  getLabelsSummary: vi.fn(async () => ({
    labels: [
      {
        id: 6,
        voxel_count: 64,
        z_start: 4,
        z_end: 6,
        state: "proposed" as const,
        origin: "unknown" as const,
        verified_at: "",
      },
    ],
    stats: { total: 1, proposed: 1, edited: 0, verified: 0 },
  })),
  getLabelIds: vi.fn(async () => plane),
  imageSlicePath: (volumeId: number, p: { axis: string; index: number }) =>
    `/image/${volumeId}/${p.axis}/${p.index}`,
  regionMaskSlicePath: (volumeId: number, p: { axis: string; index: number }) =>
    `/region/${volumeId}/${p.axis}/${p.index}`,
  getRegionIndex: vi.fn(async () => ({ axis: "z", length: 0, indices: [] })),
  getRegionLabelIds: vi.fn(async () => ({ has_region: false, ids: [] })),
  fetchLabels3DMesh: vi.fn(),
};

function mount(
  mode: "view" | "annotate" = "annotate",
  editPermission = mode === "annotate",
  onCommentLabel?: (labelId: number) => void,
) {
  return render(
    <MemoryRouter>
      <AnnotationCanvas
        taskId={5}
        volumeId={3}
        zStart={0}
        zEnd={7}
        mode={mode}
        editable={editPermission}
        api={api as never}
        onCommentLabel={onCommentLabel}
      />
    </MemoryRouter>,
  );
}

const layerInput = () =>
  document.querySelector('input[title^="Go to z layer"]') as HTMLInputElement;

/** The paint surface — the one canvas carrying the pointer handlers. */
const overlay = () =>
  [...document.querySelectorAll("canvas")].find(
    (c) => !c.hasAttribute("aria-hidden") && !c.hasAttribute("aria-label"),
  ) as HTMLCanvasElement;

/** Click the middle of the label overlay with the Select tool armed. */
async function selectOnCanvas() {
  await screen.findByRole("button", { name: "Fit window" });
  await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
  const select = screen.queryByRole("button", { name: "Select" });
  if (select) await userEvent.click(select);
  const target = overlay();
  // jsdom lays nothing out, so the component's pixel mapping needs a real box.
  target.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 4, height: 4, right: 4, bottom: 4, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
  target.setPointerCapture = () => {};
  target.releasePointerCapture = () => {};
  // A real MouseEvent, not fireEvent.pointerDown: this environment has no
  // PointerEvent, so testing-library synthesises a bare Event whose `button`
  // is undefined — and the handler's first guard is `e.button !== 0`.
  const event = new MouseEvent("pointerdown", {
    clientX: 2.5,
    clientY: 2.5,
    button: 0,
    bubbles: true,
  });
  Object.defineProperty(event, "pointerId", { value: 1 });
  act(() => {
    target.dispatchEvent(event);
  });
}

describe("Select tool label picking", () => {
  beforeEach(() => {
    hoisted.fetchObjectUrl.mockReset().mockImplementation(async (path: string) => `blob:${path}`);
    hoisted.putLabelIds.mockReset().mockResolvedValue({ max_label_id: 7, next_label_id: 8 });
    hoisted.setLabelLifecycle.mockReset().mockResolvedValue({
      label_id: 7,
      action: "verify",
      state: "verified",
      removed: false,
    });
    hoisted.createHardCase.mockReset().mockResolvedValue({
      id: 91,
      token: "token",
      task: 5,
      task_status: "in_progress",
      project: 1,
      label_id: 6,
      project_title: "Project",
      volume: 3,
      volume_name: "Volume",
      view_z: null,
      view_y: null,
      view_x: null,
      view_axis: "",
      z_start: 0,
      z_end: 7,
      status: "open",
      revoked: false,
      created_by: 4,
      created_by_username: "annotator",
      created_at: "2026-08-10T12:00:00Z",
      resolved_by: null,
      resolved_by_username: "",
      resolved_at: null,
      app_url: "/hard-cases/91",
      url: "/share/hard-case/token",
      note: "membrane is ambiguous",
      can_annotate: true,
      can_take_down: true,
      can_edit_note: true,
      can_comment: true,
      message_count: 0,
    });
    hoisted.listHardCaseMessages.mockReset().mockResolvedValue([]);
    hoisted.getTrackingPrompts.mockReset().mockResolvedValue({
      version: 1,
      items: [],
      pending_review: null,
    });
    hoisted.trackTaskBatch.mockReset();
    api.getLabelIds.mockClear();
    api.getLabelsSummary.mockClear();
  });

  it("stays on the open layer when selecting under All", async () => {
    mount();
    await selectOnCanvas();

    await waitFor(() =>
      expect((screen.getByTitle("Active label id") as HTMLInputElement).value).toBe("6"),
    );
    // Still on the task open layer (display layer 1), not the label's z_start.
    expect(layerInput().value).toBe("1");
  });

  it("stays on the open layer while the list is scoped to This layer", async () => {
    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await userEvent.click(screen.getByRole("button", { name: "This layer" }));

    await selectOnCanvas();

    await waitFor(() =>
      expect((screen.getByTitle("Active label id") as HTMLInputElement).value).toBe("6"),
    );
    expect(layerInput().value).toBe("1");
  });

  it("keeps View on the shared navigation/select stack without Track or paint side effects", async () => {
    // Even an accidentally over-permissive caller cannot turn View into an
    // editing surface: mode is the authoritative chrome/write contract.
    mount("view", true);
    await selectOnCanvas();

    await waitFor(() => {
      const activeRow = document.querySelector(".labels-list li.labels-row-active");
      expect(activeRow?.textContent?.trim().startsWith("6")).toBe(true);
    });
    expect(layerInput().value).toBe("1");
    expect(screen.queryByRole("button", { name: "Select" })).toBeNull();
    expect(screen.queryByText("Track (SAM2)")).toBeNull();
    expect(document.querySelector('canvas[aria-label="SAM tracking prompt overlay"]')).toBeNull();
    expect(hoisted.getTrackingPrompts).not.toHaveBeenCalled();
    expect(hoisted.trackTaskBatch).not.toHaveBeenCalled();

    act(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "d" })));
    await waitFor(() => expect(layerInput().value).toBe("2"));
    fireEvent.change(layerInput(), { target: { value: "4" } });
    fireEvent.keyDown(layerInput(), { key: "Enter" });
    await waitFor(() => expect(layerInput().value).toBe("4"));
  });

  it("offers only the manager label-comment action on review View", async () => {
    const onCommentLabel = vi.fn();
    mount("view", false, onCommentLabel);
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    const target = overlay();
    target.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 4, height: 4, right: 4, bottom: 4, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;

    fireEvent.contextMenu(target, { clientX: 2, clientY: 2 });
    const action = await screen.findByRole("button", { name: "Comment on label #6" });
    expect(screen.queryByRole("button", { name: "Verify" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Select" })).toBeNull();
    fireEvent.click(action);
    expect(onCommentLabel).toHaveBeenCalledWith(6);
  });

  it("has no Shift+R lifecycle action while keeping Undo and Redo", async () => {
    mount();
    await screen.findByRole("button", { name: "Fit window" });
    expect(screen.getAllByRole("button", { name: "Undo" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "Redo" }).length).toBeGreaterThan(0);

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "R", shiftKey: true }));
    });
    expect(hoisted.setLabelLifecycle).not.toHaveBeenCalled();
  });

  it("saves pending geometry before verifying it", async () => {
    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    fireEvent.change(screen.getByTitle("Active label id"), { target: { value: "6" } });
    fireEvent.click(document.querySelector('button[title^="Erase (circular)"]')!);

    const target = overlay();
    target.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 4, height: 4, right: 4, bottom: 4, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
    target.setPointerCapture = () => {};
    target.releasePointerCapture = () => {};
    const pointer = (type: string) => {
      const event = new MouseEvent(type, {
        clientX: 1.5,
        clientY: 1.5,
        button: 0,
        bubbles: true,
      });
      Object.defineProperty(event, "pointerId", { value: 1 });
      return event;
    };
    act(() => {
      target.dispatchEvent(pointer("pointerdown"));
      target.dispatchEvent(pointer("pointerup"));
    });

    fireEvent.click(screen.getByRole("button", { name: /Filters Options/ }));
    fireEvent.click(screen.getByRole("button", { name: /^✓ Verify$/ }));
    await waitFor(() => expect(hoisted.setLabelLifecycle).toHaveBeenCalledWith(5, 6, "verify"));
    expect(hoisted.putLabelIds).toHaveBeenCalled();
    expect(hoisted.putLabelIds.mock.invocationCallOrder[0]).toBeLessThan(
      hoisted.setLabelLifecycle.mock.invocationCallOrder[0],
    );
  });

  it("keeps the last known label state when a summary refresh fails", async () => {
    mount();
    await screen.findByTitle(/64 voxels/);
    const calls = api.getLabelsSummary.mock.calls.length;
    api.getLabelsSummary.mockRejectedValueOnce(new Error("temporary network failure"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    await waitFor(() => expect(api.getLabelsSummary.mock.calls.length).toBeGreaterThan(calls));
    await act(async () => {});
    expect(screen.getByTitle(/64 voxels/)).toBeTruthy();
  });

  it("records a hard case with the optional note from the confirmation dialog", async () => {
    mount();
    await screen.findByTitle(/64 voxels/);
    fireEvent.change(screen.getByTitle("Active label id"), { target: { value: "6" } });

    fireEvent.click(screen.getByRole("button", { name: "Record hard case" }));
    const note = await screen.findByPlaceholderText("Add a short note for collaborators");
    fireEvent.change(note, { target: { value: "  membrane is ambiguous  " } });
    fireEvent.click(screen.getByRole("button", { name: "Share with the project" }));

    await waitFor(() => {
      expect(hoisted.createHardCase).toHaveBeenCalledWith(
        5,
        6,
        "membrane is ambiguous",
        { z: 0, y: 2, x: 2, axis: "z", label: 6 },
        // No category chosen: blank is a real value, not a placeholder the
        // client is expected to fill in.
        "",
      );
    });
    expect(await screen.findByText("Hard case recorded")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit notes" }));
    expect(await screen.findByRole("dialog", { name: "Notes · label #6" })).toBeTruthy();
    expect(screen.getByLabelText("Primary note")).toBeTruthy();
  });

  it("records the chosen category, and lets it be cleared again", async () => {
    mount();
    await screen.findByTitle(/64 voxels/);
    fireEvent.change(screen.getByTitle("Active label id"), { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: "Record hard case" }));

    const chip = await screen.findByRole("button", { name: "Needs split" });
    fireEvent.click(chip);
    expect(chip.getAttribute("aria-pressed")).toBe("true");
    // Clicking the selected chip again clears it: a reason nobody is sure of
    // belongs recorded as absent, not guessed.
    fireEvent.click(chip);
    expect(chip.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(chip);

    fireEvent.click(screen.getByRole("button", { name: "Share with the project" }));
    await waitFor(() => {
      expect(hoisted.createHardCase).toHaveBeenCalledWith(
        5,
        6,
        "",
        { z: 0, y: 2, x: 2, axis: "z", label: 6 },
        "needs_split",
      );
    });
  });
});
