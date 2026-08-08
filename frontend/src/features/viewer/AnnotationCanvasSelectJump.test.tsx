import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

const hoisted = vi.hoisted(() => ({ fetchObjectUrl: vi.fn(), setLabelLifecycle: vi.fn() }));

vi.mock("../../api/viewer", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/viewer")>()),
  fetchObjectUrl: hoisted.fetchObjectUrl,
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
    <AnnotationCanvas taskId={5} volumeId={3} zStart={0} zEnd={7} editable api={api as never} />,
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
    hoisted.setLabelLifecycle.mockReset();
    api.getLabelIds.mockClear();
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
});
