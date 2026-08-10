import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AnnotationCanvas from "./AnnotationCanvas";

/**
 * Picking a label with the **Select** tool must behave like clicking its row in
 * the Labels list: it sets Active, and in the **All** scope it also jumps to
 * where that label starts. Before this, the canvas half only set Active — the
 * same gesture did less depending on where you performed it.
 */

vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: { id: 4 } }) }));

vi.mock("../rendering", () => ({
  phase14ChunkRendererEnabled: () => false,
  chunkFallbackMessage: () => "",
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
  getTrackingPrompts: vi.fn(async () => ({ version: 1, items: [], pending_review: null })),
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

function mount() {
  return render(
    <MemoryRouter>
      <AnnotationCanvas taskId={5} volumeId={3} zStart={0} zEnd={7} editable api={api as never} />
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
  await userEvent.click(screen.getByRole("button", { name: "Select" }));
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
    api.getLabelIds.mockClear();
    api.getLabelsSummary.mockClear();
  });

  it("jumps to where the label starts while the list is scoped to All", async () => {
    mount();
    await selectOnCanvas();

    // z_start 4 is layer 5 to a human (`layerIndex.displayLayer`).
    await waitFor(() => expect(layerInput().value).toBe("5"));
    expect((screen.getByTitle("Active label id") as HTMLInputElement).value).toBe("6");
  });

  it("stays on the open layer while the list is scoped to This layer", async () => {
    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await userEvent.click(screen.getByRole("button", { name: "This layer" }));

    await selectOnCanvas();

    // Same rule as clicking a "This layer" row: select, do not navigate.
    await waitFor(() =>
      expect((screen.getByTitle("Active label id") as HTMLInputElement).value).toBe("6"),
    );
    expect(layerInput().value).toBe("1");
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
    api.getLabelsSummary.mockRejectedValueOnce(new Error("temporary network failure"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    await screen.findByRole("alert");
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
      );
    });
    expect(await screen.findByText("Hard case recorded")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit notes" }));
    expect(await screen.findByRole("dialog", { name: "Notes · label #6" })).toBeTruthy();
    expect(screen.getByLabelText("Primary note")).toBeTruthy();
  });
});
