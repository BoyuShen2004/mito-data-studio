import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import AnnotationCanvas from "./AnnotationCanvas";

/**
 * Several tracking seeds on one layer.
 *
 * The regression this pins: committing a Box/Point proposal wrote the
 * prediction back as *the* layer mask, so every commit erased the objects
 * committed before it and a layer could only ever hold one prompt. The backend
 * splits a layer into branches by connected component, so wiping the earlier
 * blobs meant that inference could never see more than one branch.
 *
 * This drives the real component — tool button, canvas pointer events, the
 * Enter finalize — and asserts on the seed actually sent to the server, rather
 * than on the shape of the source.
 */

const track = vi.hoisted(() => ({
  getTrackingPrompts: vi.fn(),
  replaceTrackingPrompts: vi.fn(),
  putTrackingPrompt: vi.fn(),
  trackTaskBatch: vi.fn(),
  reviewTrackingPreview: vi.fn(),
  predictMaskFromPoints: vi.fn(),
  predictMaskFromBox: vi.fn(),
  fetchObjectUrl: vi.fn(),
}));

vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: { id: 4 } }) }));

vi.mock("../rendering", () => ({
  phase14ChunkRendererEnabled: () => false,
  chunkFallbackMessage: () => "",
  ChunkRenderedImageSource: class {},
}));

vi.mock("./Labels3DPanel", () => ({ default: () => <div /> }));

vi.mock("../../api/viewer", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/viewer")>()),
  fetchObjectUrl: track.fetchObjectUrl,
  getTrackingPrompts: track.getTrackingPrompts,
  replaceTrackingPrompts: track.replaceTrackingPrompts,
  putTrackingPrompt: track.putTrackingPrompt,
  trackTaskBatch: track.trackTaskBatch,
  reviewTrackingPreview: track.reviewTrackingPreview,
  predictMaskFromPoints: track.predictMaskFromPoints,
  predictMaskFromBox: track.predictMaskFromBox,
}));

const meta = {
  shape: { z: 4, y: 4, x: 4 },
  dtype: "uint8",
  axes: ["z", "y", "x"],
  has_label: true,
  has_region_mask: false,
  volume_id: 3,
  ready_streaming: false,
  region_ready_streaming: false,
  display_range: { lo: 0, hi: 255 },
};

const emptyPlane = { shape: [4, 4] as [number, number], runs: [[0, 16]] as [number, number][] };

/** A queued class with nothing drawn on it yet. */
const emptyPrompt = {
  parent_id: 9,
  subclasses: [{ index: 1, seeds: [] as { z: number; rle: [number, number][]; shape: [number, number] }[] }],
  start_z: 0,
  end_z: 0,
  z_range: [0, 0] as [number, number],
  status: "draft" as const,
};

/** Value-RLE ([id, count]) as the predict endpoint returns it. */
const topLeftPair = [[1, 2], [0, 14]] as [number, number][];      // pixels 0,1
const bottomRightPair = [[0, 14], [1, 2]] as [number, number][];  // pixels 14,15

const api = {
  getVolumeMeta: vi.fn(async () => meta),
  getLabelState: vi.fn(async () => ({ max_label_id: 2, next_label_id: 3 })),
  getLabelsSummary: vi.fn(async () => ({
    labels: [],
    stats: { total: 0, proposed: 0, edited: 0, verified: 0 },
  })),
  getLabelIds: vi.fn(async () => emptyPlane),
  imageSlicePath: (volumeId: number, p: { axis: string; index: number }) =>
    `/image/${volumeId}/${p.axis}/${p.index}`,
  regionMaskSlicePath: (volumeId: number, p: { axis: string; index: number }) =>
    `/region/${volumeId}/${p.axis}/${p.index}`,
  getRegionIndex: vi.fn(async () => ({ axis: "z", length: 0, indices: [] })),
  getRegionLabelIds: vi.fn(async () => ({ has_region: false, ids: [] })),
  fetchLabels3DMesh: vi.fn(),
};

// jsdom gives every element a zero-sized rect, which makes `pixelFromEvent`
// reject each click as outside the image. Give the canvases a real box so a
// click maps to a pixel the way it does in a browser.
const rect = {
  left: 0, top: 0, width: 400, height: 400, right: 400, bottom: 400, x: 0, y: 0,
  toJSON: () => ({}),
} as DOMRect;
const originalRect = HTMLCanvasElement.prototype.getBoundingClientRect;
const originalCapture = HTMLCanvasElement.prototype.setPointerCapture;
const originalGetContext = HTMLCanvasElement.prototype.getContext;

/** Every `putImageData` the tracking overlay performed, newest last. */
let painted: Uint8ClampedArray[] = [];

/** Distinct "r,g,b" of every visible pixel the overlay painted. */
function paintedColors(): Set<string> {
  const colors = new Set<string>();
  for (const data of painted) {
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] > 0) colors.add(`${data[i]},${data[i + 1]},${data[i + 2]}`);
    }
  }
  return colors;
}

/** The pending Box/Point proposal is drawn in this green, seeds are not. */
const PROPOSAL_GREEN = "34,197,94";

beforeEach(() => {
  painted = [];
  HTMLCanvasElement.prototype.getBoundingClientRect = () => rect;
  HTMLCanvasElement.prototype.setPointerCapture = () => {};
  HTMLCanvasElement.prototype.getContext = function getContext(this: HTMLCanvasElement, ...args) {
    const ctx = (originalGetContext as never as (...a: unknown[]) => CanvasRenderingContext2D)
      .apply(this, args as never);
    if (ctx && this.getAttribute("aria-label") === "SAM tracking prompt overlay") {
      ctx.putImageData = ((image: ImageData) => { painted.push(image.data); }) as never;
    }
    return ctx as never;
  } as typeof HTMLCanvasElement.prototype.getContext;
});

afterAll(() => {
  HTMLCanvasElement.prototype.getBoundingClientRect = originalRect;
  HTMLCanvasElement.prototype.setPointerCapture = originalCapture;
  HTMLCanvasElement.prototype.getContext = originalGetContext;
});

/**
 * Dispatch a pointer event that actually carries coordinates.
 *
 * jsdom has no usable `PointerEvent`, and `fireEvent.pointerDown` drops
 * `clientX`/`clientY` on the floor — every click would land on `NaN`, which
 * silently satisfies "outside the current proposal" and makes the refinement
 * path untestable. A `MouseEvent` named `pointerdown` reaches React's
 * `onPointerDown` with its coordinates intact.
 */
function pointer(
  el: Element,
  type: "pointerdown" | "pointermove" | "pointerup",
  init: MouseEventInit,
) {
  const ev = new MouseEvent(type, { bubbles: true, cancelable: true, ...init });
  Object.defineProperty(ev, "pointerId", { value: 1 });
  el.dispatchEvent(ev);
}

function mount(zStart = 0) {
  return render(
    <AnnotationCanvas taskId={5} volumeId={3} zStart={zStart} zEnd={3} editable api={api as never} />,
  );
}

/** The seed runs of the most recent durable save, or null if nothing saved. */
function lastSavedSeedRuns(): [number, number][] | null {
  const calls = track.putTrackingPrompt.mock.calls;
  if (!calls.length) return null;
  const prompt = calls[calls.length - 1][1] as typeof emptyPrompt;
  const seeds = prompt.subclasses.flatMap((child) => child.seeds);
  return seeds.find((seed) => seed.z === 0)?.rle ?? [];
}

describe("Annotate Point Mask prompts", () => {
  beforeEach(() => {
    for (const fn of Object.values(track)) fn.mockReset();
    track.fetchObjectUrl.mockImplementation(async (path: string) => `blob:${path}`);
    track.getTrackingPrompts.mockResolvedValue({ version: 1, items: [emptyPrompt], pending_review: null });
    track.predictMaskFromPoints.mockResolvedValue({ shape: [4, 4], runs: topLeftPair });
  });

  it("keeps the hover tip as chrome only (does not re-predict on move)", async () => {
    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: /^Point Mask$/ }));
    const overlay = document.querySelector(".canvas-stage canvas:not([aria-label])")!;

    // Nothing is proposed before the first click.
    pointer(overlay, "pointermove", { clientX: 210, clientY: 210 });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(track.predictMaskFromPoints).not.toHaveBeenCalled();

    pointer(overlay, "pointerdown", { clientX: 110, clientY: 110 });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(1));
    expect(track.predictMaskFromPoints.mock.calls[0][3]).toEqual([[1, 1]]);
    expect(track.predictMaskFromPoints.mock.calls[0][4]).toEqual([1]);

    // Hover tip must not become a second positive prompt — that drifted the
    // mask away from the click on dense EM (podo).
    pointer(overlay, "pointermove", { clientX: 390, clientY: 390 });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(1);
  });

  it("does not predict a negative hover tip on Alt+move", async () => {
    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: /^Point Mask$/ }));
    const overlay = document.querySelector(".canvas-stage canvas:not([aria-label])")!;

    pointer(overlay, "pointerdown", { clientX: 110, clientY: 110 });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(1));

    pointer(overlay, "pointermove", { clientX: 390, clientY: 390, altKey: true });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(1);
  });

  it("commits Alt-click as a negative point, not as the hovered preview", async () => {
    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: /^Point Mask$/ }));
    const overlay = document.querySelector(".canvas-stage canvas:not([aria-label])")!;

    pointer(overlay, "pointerdown", { clientX: 110, clientY: 110 });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(1));

    pointer(overlay, "pointerdown", { clientX: 390, clientY: 390, altKey: true });
    await waitFor(() =>
      expect(
        track.predictMaskFromPoints.mock.calls.some(
          (call) =>
            JSON.stringify(call[3]) === JSON.stringify([[1, 1], [3, 3]]) &&
            JSON.stringify(call[4]) === JSON.stringify([1, 0]),
        ),
      ).toBe(true),
    );
  });
});

/** Click the tracking overlay at an image pixel, then finalize with Enter. */
async function seedAt(clientX: number, clientY: number) {
  const overlay = screen.getByLabelText("SAM tracking prompt overlay");
  const saves = track.putTrackingPrompt.mock.calls.length;
  pointer(overlay, "pointerdown", { clientX, clientY });
  // The prediction is a network round trip; Enter must land after it stages.
  await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalled());
  fireEvent.keyDown(window, { key: "Enter" });
  await waitFor(() => expect(track.putTrackingPrompt.mock.calls.length).toBeGreaterThan(saves));
}

/** Drag a box across the image, the way the Box tool is actually used. */
async function boxFrom(x0: number, y0: number, x1: number, y1: number) {
  const overlay = screen.getByLabelText("SAM tracking prompt overlay");
  const predictions = track.predictMaskFromBox.mock.calls.length;
  pointer(overlay, "pointerdown", { clientX: x0, clientY: y0 });
  pointer(overlay, "pointermove", { clientX: x1, clientY: y1 });
  pointer(overlay, "pointerup", { clientX: x1, clientY: y1 });
  await waitFor(() =>
    expect(track.predictMaskFromBox.mock.calls.length).toBeGreaterThan(predictions));
}

describe("Track seeds on one layer", () => {
  beforeEach(() => {
    for (const fn of Object.values(track)) fn.mockReset();
    track.fetchObjectUrl.mockImplementation(async (path: string) => `blob:${path}`);
    track.getTrackingPrompts.mockResolvedValue({ version: 1, items: [emptyPrompt], pending_review: null });
    track.replaceTrackingPrompts.mockImplementation(async (_id: number, items: unknown[]) => ({ version: 1, items }));
    track.putTrackingPrompt.mockImplementation(async (_id: number, prompt: unknown) => prompt);
  });

  it("keeps every committed object instead of replacing the layer each time", async () => {
    track.predictMaskFromPoints
      .mockResolvedValueOnce({ shape: [4, 4], runs: topLeftPair })
      .mockResolvedValueOnce({ shape: [4, 4], runs: bottomRightPair });

    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await screen.findByText("Class 9");
    await userEvent.click(screen.getByRole("button", { name: "Point" }));

    // First object, top-left.
    await seedAt(10, 10);
    expect(lastSavedSeedRuns()).toEqual([[0, 2]]);

    // Second object, bottom-right and nowhere near the first.
    await seedAt(390, 390);
    // Before the fix this was [[14, 2]] — the first object was gone.
    expect(lastSavedSeedRuns()).toEqual([[0, 2], [14, 2]]);
  });

  it("does not leave a rejected seed painted on the canvas", async () => {
    // A refused save used to refetch the queue but keep the local drawing
    // surface, so the strokes stayed painted and a failed save looked exactly
    // like a successful one.
    track.predictMaskFromPoints.mockResolvedValue({ shape: [4, 4], runs: topLeftPair });
    track.putTrackingPrompt.mockRejectedValue(new Error("Seed rejected"));

    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await screen.findByText("Class 9");
    await userEvent.click(screen.getByRole("button", { name: "Point" }));

    const overlay = screen.getByLabelText("SAM tracking prompt overlay");
    pointer(overlay, "pointerdown", { clientX: 10, clientY: 10 });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalled());
    fireEvent.keyDown(window, { key: "Enter" });

    // The failure is surfaced and the queue re-read from the server.
    await screen.findByText("Seed rejected");
    await waitFor(() => expect(track.getTrackingPrompts.mock.calls.length).toBeGreaterThan(1));
    expect(screen.getByText("no seeds")).toBeTruthy();

    // What the overlay paints from here on is the question. The pending green
    // proposal rightly stays — the save failed, so Enter can retry it. What must
    // be gone is any *seed*-coloured pixel, which is what the canvas uses to say
    // "this is committed".
    painted = [];
    pointer(overlay, "pointermove", { clientX: 20, clientY: 20 });
    await waitFor(() => expect(painted.length).toBeGreaterThan(0));
    expect([...paintedColors()].filter((c) => c !== PROPOSAL_GREEN)).toEqual([]);
  });

  it("keeps both boxes when they are drawn one after another", async () => {
    // How the Box tool is actually used: draw one object, draw the next, and
    // expect both to stay -- exactly how Brush behaves. Requiring Enter between
    // them meant the second drag silently threw the first away.
    track.predictMaskFromBox
      .mockResolvedValueOnce({ shape: [4, 4], runs: topLeftPair })
      .mockResolvedValueOnce({ shape: [4, 4], runs: bottomRightPair });

    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await screen.findByText("Class 9");
    await userEvent.click(screen.getByRole("button", { name: "Box" }));

    await boxFrom(10, 10, 210, 210);
    await boxFrom(190, 190, 390, 390);
    fireEvent.keyDown(window, { key: "Enter" });
    await waitFor(() => expect(track.putTrackingPrompt).toHaveBeenCalled());

    expect(lastSavedSeedRuns()).toEqual([[0, 2], [14, 2]]);
  });

  it("keeps both point objects when they are clicked one after another", async () => {
    // The same expectation as Box: click one mitochondrion, click the next, and
    // both stay. No Enter in between.
    track.predictMaskFromPoints
      .mockResolvedValueOnce({ shape: [4, 4], runs: topLeftPair })
      .mockResolvedValueOnce({ shape: [4, 4], runs: bottomRightPair });

    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await screen.findByText("Class 9");
    await userEvent.click(screen.getByRole("button", { name: "Point" }));

    const overlay = screen.getByLabelText("SAM tracking prompt overlay");
    pointer(overlay, "pointerdown", { clientX: 10, clientY: 10 });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(1));
    // Pixel 15 — outside the first proposal, so a different object.
    pointer(overlay, "pointerdown", { clientX: 390, clientY: 390 });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(2));
    fireEvent.keyDown(window, { key: "Enter" });
    await waitFor(() => expect(track.putTrackingPrompt).toHaveBeenCalled());

    expect(lastSavedSeedRuns()).toEqual([[0, 2], [14, 2]]);
  });

  it("still refines one object when the click lands on it", async () => {
    // The accumulate rule must not cost the ability to correct a bad mask: a
    // click the proposal already covers is a refinement, and so is any
    // Alt-click, so both go to the server as one growing point set.
    track.predictMaskFromPoints.mockResolvedValue({ shape: [4, 4], runs: topLeftPair });

    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await screen.findByText("Class 9");
    await userEvent.click(screen.getByRole("button", { name: "Point" }));

    const overlay = screen.getByLabelText("SAM tracking prompt overlay");
    // Pixel 0, then pixel 1 — inside the proposal the first click produced.
    pointer(overlay, "pointerdown", { clientX: 10, clientY: 10 });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(1));
    pointer(overlay, "pointerdown", { clientX: 110, clientY: 10 });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(2));
    // An Alt-click outside it is a negative correction, not a new object.
    pointer(overlay, "pointerdown", { clientX: 390, clientY: 390, altKey: true });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(3));

    // One growing point set, and nothing banked behind it.
    const [, , , points, labels] = track.predictMaskFromPoints.mock.calls[2];
    expect(points).toEqual([[0, 0], [1, 0], [3, 3]]);
    expect(labels).toEqual([1, 1, 0]);
    expect(track.putTrackingPrompt).not.toHaveBeenCalled();
  });

  it("accumulates when each object is refined with several clicks then finalized", async () => {
    // The sequence the access log shows in real use: click, refine, refine,
    // Enter -- then the same again on the next mitochondrion.
    track.predictMaskFromPoints
      .mockResolvedValueOnce({ shape: [4, 4], runs: topLeftPair })
      .mockResolvedValueOnce({ shape: [4, 4], runs: topLeftPair })
      .mockResolvedValueOnce({ shape: [4, 4], runs: bottomRightPair })
      .mockResolvedValueOnce({ shape: [4, 4], runs: bottomRightPair });

    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await screen.findByText("Class 9");
    await userEvent.click(screen.getByRole("button", { name: "Point" }));
    const overlay = screen.getByLabelText("SAM tracking prompt overlay");

    // First object: click pixel 0, refine on pixel 1 (inside it), finalize.
    pointer(overlay, "pointerdown", { clientX: 10, clientY: 10 });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(1));
    pointer(overlay, "pointerdown", { clientX: 110, clientY: 10 });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(2));
    fireEvent.keyDown(window, { key: "Enter" });
    await waitFor(() => expect(lastSavedSeedRuns()).toEqual([[0, 2]]));

    // Second object, far away: same shape of interaction.
    pointer(overlay, "pointerdown", { clientX: 290, clientY: 390 });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(3));
    pointer(overlay, "pointerdown", { clientX: 390, clientY: 390 });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalledTimes(4));
    fireEvent.keyDown(window, { key: "Enter" });

    await waitFor(() => expect(track.putTrackingPrompt.mock.calls.length).toBeGreaterThan(1));
    expect(lastSavedSeedRuns()).toEqual([[0, 2], [14, 2]]);
  });

  it("widens the range to the layer being drawn on, so Propagate stays reachable", async () => {
    // Regression: queueing a class pins Start/End to the single layer it was
    // queued on. A seed drawn on any other layer then fell outside that range,
    // which disabled Propagate -- a real session saved seed after seed and
    // never sent one propagate request.
    track.getTrackingPrompts.mockResolvedValue({
      version: 1,
      items: [{ ...emptyPrompt, start_z: 0, end_z: 0, z_range: [0, 0] as [number, number] }],
      pending_review: null,
    });
    track.predictMaskFromPoints.mockResolvedValue({ shape: [4, 4], runs: topLeftPair });

    mount(2);  // the editor opens on layer 2; the class is pinned to layer 0
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await screen.findByText("Class 9");
    await userEvent.click(screen.getByRole("button", { name: "Point" }));

    const overlay = screen.getByLabelText("SAM tracking prompt overlay");
    pointer(overlay, "pointerdown", { clientX: 10, clientY: 10 });
    await waitFor(() => expect(track.predictMaskFromPoints).toHaveBeenCalled());
    fireEvent.keyDown(window, { key: "Enter" });
    await waitFor(() => expect(track.putTrackingPrompt).toHaveBeenCalled());

    const calls = track.putTrackingPrompt.mock.calls;
    const saved = calls[calls.length - 1][1] as { start_z: number; end_z: number };
    expect([saved.start_z, saved.end_z]).toEqual([0, 2]);
    await waitFor(() =>
      expect((screen.getByRole("button", { name: "Propagate selected" }) as HTMLButtonElement).disabled)
        .toBe(false));
  });

  it("sends the two objects as one seed so the server can split them", async () => {
    // The layer holds a single seed mask; separating it into branches is the
    // backend's connected-component job, and it can only do that if both blobs
    // are still in the mask it receives.
    track.predictMaskFromPoints
      .mockResolvedValueOnce({ shape: [4, 4], runs: topLeftPair })
      .mockResolvedValueOnce({ shape: [4, 4], runs: bottomRightPair });

    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await screen.findByText("Class 9");
    await userEvent.click(screen.getByRole("button", { name: "Point" }));
    await seedAt(10, 10);
    await seedAt(390, 390);

    const prompt = track.putTrackingPrompt.mock.calls[track.putTrackingPrompt.mock.calls.length - 1][1] as typeof emptyPrompt;
    const seeds = prompt.subclasses.flatMap((child) => child.seeds).filter((seed) => seed.z === 0);
    expect(seeds).toHaveLength(1);
    // Two separated runs, and the gap between them is what makes them two
    // 8-connected components once the server looks at the 4x4 grid.
    expect(seeds[0].rle).toEqual([[0, 2], [14, 2]]);
    expect(prompt.status).toBe("ready");
  });
});
