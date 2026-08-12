import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AnnotationCanvas, { type AxisControls } from "./AnnotationCanvas";
import { ApiError } from "../../api/client";

/**
 * Region only is a strict focus filter and must never vandalize labels it
 * hides. Empty outside pixels may still stage pending work for leaving the
 * mode, subject to the Overwrite policy.
 *
 * These drive the real canvas — paint through its pointer handlers, toggle the
 * mode through the same `AxisControls` handle the topbar uses, and read back
 * what Save would commit. The pure projection rules live in
 * `outsideRegionEdits.test.ts`; these cases pin the canvas wiring.
 */

const harness = vi.hoisted(() => ({
  decodeRegionMask: vi.fn(),
  fetchObjectUrl: vi.fn(),
  putLabelIds: vi.fn(),
  runSplitComponents: vi.fn(),
  confirm: vi.fn(),
  overlayPixels: null as Uint8ClampedArray | null,
}));

vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: { id: 4 } }) }));

vi.mock("../rendering", () => ({
  phase14ChunkRendererEnabled: () => false,
  chunkFallbackMessage: () => "",
  ChunkRenderedImageSource: class {},
}));

vi.mock("./regionOverlap", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./regionOverlap")>()),
  decodeRegionMask: harness.decodeRegionMask,
}));

vi.mock("./Labels3DPanel", () => ({ default: () => <div /> }));

vi.mock("../../api/viewer", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/viewer")>()),
  fetchObjectUrl: harness.fetchObjectUrl,
  putLabelIds: harness.putLabelIds,
  runSplitComponents: harness.runSplitComponents,
}));

const W = 8;
const H = 8;
const baseGetContext = HTMLCanvasElement.prototype.getContext;

/** ROI covers the left half of the plane; x >= 4 is outside it. */
const ROI = Uint8Array.from(
  Array.from({ length: H * W }, (_, i) => (i % W < 4 ? 1 : 0)),
);

/** Stored labels: instance 5 fills one pixel outside the ROI, at (row 1, x 6). */
const STORED_AT = 1 * W + 6;

const meta = {
  shape: { z: 6, y: H, x: W },
  dtype: "uint8",
  axes: ["z", "y", "x"],
  has_label: true,
  has_region_mask: true,
  volume_id: 3,
  ready_streaming: false,
  region_ready_streaming: false,
  display_range: { lo: 0, hi: 255 },
};

const storedRuns = (): [number, number][] => [
  [0, STORED_AT],
  [5, 1],
  [0, H * W - STORED_AT - 1],
];

const api = {
  getVolumeMeta: vi.fn(async () => meta),
  getLabelState: vi.fn(async () => ({ max_label_id: 5, next_label_id: 6 })),
  getLabelsSummary: vi.fn(async () => ({
    labels: [],
    stats: { total: 0, proposed: 0, edited: 0, verified: 0 },
  })),
  getLabelIds: vi.fn(async () => ({
    shape: [H, W] as [number, number],
    runs: storedRuns(),
    revision: undefined as string | undefined,
  })),
  imageSlicePath: () => "/image",
  regionMaskSlicePath: () => "/region",
  getRegionIndex: vi.fn(async () => ({ axis: "z", length: 0, indices: [] })),
  // Volume-wide Region only membership; these fixtures exercise the
  // per-plane half, so the server half is deliberately "no ROI known".
  getRegionLabelIds: vi.fn(async () => ({ has_region: false, ids: [] })),
  fetchLabels3DMesh: vi.fn(),
};

/** The paint surface: the one canvas carrying the pointer handlers (the cursor
 * layer is aria-hidden and the tracking overlay is aria-labelled). */
const overlay = () =>
  [...document.querySelectorAll("canvas")].find(
    (c) => !c.hasAttribute("aria-hidden") && !c.hasAttribute("aria-label"),
  ) as HTMLCanvasElement;

function selectBrush() {
  fireEvent.click(
    document.querySelector('button[title^="Paint the active instance"]') as HTMLButtonElement,
  );
}

function selectErase() {
  fireEvent.click(
    document.querySelector('button[title^="Erase (circular)"]') as HTMLButtonElement,
  );
}

function clickCanvasUndo() {
  const button = [...document.querySelectorAll(".tool-strip button")].find(
    (candidate) => candidate.textContent === "Undo",
  );
  if (!button) throw new Error("canvas Undo button missing");
  fireEvent.click(button);
}

/** Paint one pixel at image coordinate (row, col) with the active tool. */
function paintPixel(row: number, col: number) {
  const target = overlay();
  // jsdom lays nothing out, so the component's pixel mapping needs a real box
  // and pointer capture needs to exist for the stroke handler to run.
  target.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: W, height: H, right: W, bottom: H, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
  target.setPointerCapture = () => {};
  target.releasePointerCapture = () => {};
  // A real MouseEvent, not fireEvent.pointerDown: this environment has no
  // PointerEvent, so testing-library synthesises a bare Event whose `button`
  // is undefined — and the handler's first guard is `e.button !== 0`.
  const pointer = (type: string) => {
    const event = new MouseEvent(type, {
      clientX: col + 0.5,
      clientY: row + 0.5,
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
}

function mount() {
  let controls: AxisControls | null = null;
  const view = render(
    <AnnotationCanvas
      taskId={5}
      volumeId={3}
      zStart={0}
      zEnd={5}
      editable
      api={api as never}
      onAxisControls={(next) => {
        controls = next ?? controls;
      }}
    />,
  );
  return {
    view,
    regionOnly: (on: boolean) => act(() => controls?.changeRegionOnly(on)),
    overwrite: (mode: "overwrite_empty" | "overwrite_all") =>
      act(() => controls?.changeRegionOverwriteMode(mode)),
  };
}

/** The tool strip's Save button — named by its title, since other chrome also
 * carries a button whose accessible name starts with "Save". */
function clickSave() {
  const save = document.querySelector(
    'button[title^="Write every edited layer"]',
  ) as HTMLButtonElement;
  save.click();
}

/** The plane `putLabelIds` was last asked to write, decoded back to ids. */
function lastSavedPlane(): number[] {
  const call = harness.putLabelIds.mock.lastCall;
  if (!call) throw new Error("nothing was saved");
  const runs = call[4] as [number, number][];
  const out: number[] = [];
  for (const [id, count] of runs) for (let i = 0; i < count; i++) out.push(id);
  return out;
}

describe("Region only and edits outside the region", () => {
  beforeEach(() => {
    window.localStorage.clear();
    harness.decodeRegionMask.mockReset().mockResolvedValue(ROI);
    harness.fetchObjectUrl.mockReset().mockImplementation(async (p: string) => `blob:${p}`);
    harness.putLabelIds
      .mockReset()
      .mockResolvedValue({ max_label_id: 9, next_label_id: 10 });
    harness.runSplitComponents.mockReset();
    api.getLabelsSummary.mockReset().mockResolvedValue({
      labels: [],
      stats: { total: 0, proposed: 0, edited: 0, verified: 0 },
    });
    api.getLabelIds.mockReset().mockResolvedValue({
      shape: [H, W] as [number, number],
      runs: storedRuns(),
      revision: undefined,
    });
    harness.confirm.mockReset().mockReturnValue(true);
    harness.overlayPixels = null;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function (
      this: HTMLCanvasElement,
      ...args: Parameters<HTMLCanvasElement["getContext"]>
    ) {
      const context = baseGetContext.apply(this, args as never);
      if (context && args[0] === "2d") {
        (context as CanvasRenderingContext2D).putImageData = (image: ImageData) => {
          if (image.width === W && image.height === H) {
            harness.overlayPixels = image.data.slice();
          }
        };
      }
      return context as never;
    });
    vi.spyOn(window, "confirm").mockImplementation(harness.confirm);
  });

  it("records paint outside the region only while Region only is on", async () => {
    const { regionOnly } = mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });
    selectBrush();

    // Region only off: ordinary painting, nothing recorded — this is the path
    // every annotator who never touches the mode takes.
    paintPixel(1, 6);
    await act(async () => {
      await Promise.resolve();
    });
    regionOnly(true);
    await waitFor(() => expect(harness.decodeRegionMask).toHaveBeenCalled());

    // Saving now must not warn: nothing was painted outside *under the mode*.
    await act(async () => {
      clickSave();
    });
    expect(harness.confirm).not.toHaveBeenCalled();
  });

  it("does not roll the save token back when navigating to a prefetched plane", async () => {
    api.getLabelIds.mockResolvedValue({
      shape: [H, W] as [number, number],
      runs: storedRuns(),
      revision: "revision-before-first-save",
    });
    harness.putLabelIds
      .mockResolvedValueOnce({
        max_label_id: 9,
        next_label_id: 10,
        revision: "revision-after-first-save",
      })
      .mockResolvedValueOnce({
        max_label_id: 9,
        next_label_id: 10,
        revision: "revision-after-second-save",
      });

    mount();
    await screen.findByRole("button", { name: "Fit window" });
    // The settled-slice prefetch fills z=1 with the original plane revision.
    await waitFor(() => expect(api.getLabelIds.mock.calls.length).toBeGreaterThan(1));
    selectBrush();
    paintPixel(0, 0);
    await act(async () => clickSave());
    await waitFor(() => expect(harness.putLabelIds).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByTitle("Next layer"));
    await waitFor(() => expect(screen.getByTitle(/^z 2\/6$/)).toBeTruthy());
    // Navigation state advances immediately; the cached plane installs on the
    // canvas through the debounced load effect one frame later.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    paintPixel(0, 1);
    await act(async () => clickSave());
    await waitFor(() => expect(harness.putLabelIds).toHaveBeenCalledTimes(2));

    // putLabelIds(..., expectedRevision): the cached z=1 pixels are reusable,
    // but their old volume token must not replace the first Save's response.
    expect(harness.putLabelIds.mock.calls[1][7]).toBe("revision-after-first-save");
  });

  it("reloads and retains only non-overlapping pending edits after a real conflict", async () => {
    let newerWorkExists = false;
    api.getLabelIds.mockImplementation(async () => newerWorkExists
      ? {
          shape: [H, W] as [number, number],
          runs: [[0, H * W - 1], [9, 1]] as [number, number][],
          revision: "newer-server-revision",
        }
      : {
          shape: [H, W] as [number, number],
          runs: [[0, H * W]] as [number, number][],
          revision: "original-revision",
        });
    harness.putLabelIds
      .mockImplementationOnce(async () => {
        newerWorkExists = true;
        throw new ApiError(409, "working volume changed", { reason: "write_conflict" });
      })
      .mockResolvedValueOnce({
        max_label_id: 9,
        next_label_id: 10,
        revision: "after-recovered-save",
      });
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});

    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    selectBrush();
    paintPixel(0, 0);
    await act(async () => clickSave());

    await waitFor(() => expect(alert).toHaveBeenCalledWith(
      expect.stringContaining("reloaded the latest working volume"),
    ));
    await act(async () => clickSave());
    await waitFor(() => expect(harness.putLabelIds).toHaveBeenCalledTimes(2));

    const saved = lastSavedPlane();
    expect(saved[0]).toBe(6); // this tab's non-overlapping brush edit survived
    expect(saved[H * W - 1]).toBe(9); // newer disk work was not overwritten
    expect(harness.putLabelIds.mock.calls[1][7]).toBe("newer-server-revision");
  });

  it("cannot paint through a verified label even while Hide Verified hides it", async () => {
    api.getLabelsSummary.mockResolvedValue({
      labels: [{
        id: 5,
        voxel_count: 1,
        z_start: 0,
        z_end: 0,
        state: "verified",
        origin: "manual",
        verified_at: "2026-08-10T00:00:00Z",
      }],
      stats: { total: 1, proposed: 0, edited: 0, verified: 1 },
    } as never);
    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(screen.getByLabelText("Hide Verified")).toBeTruthy());
    await screen.findByText(/●1/);
    // Ignore any mount-time synchronization; this assertion is specifically
    // about whether the hidden verified pixel can create a pending user edit.
    harness.putLabelIds.mockClear();
    fireEvent.click(screen.getByLabelText("Hide Verified"));
    fireEvent.change(screen.getByTitle("Active label id"), { target: { value: "6" } });
    selectBrush();
    paintPixel(1, 6);
    await act(async () => clickSave());

    // The brush may paint empty neighbours, but the hidden verified voxel at
    // its centre must survive byte-for-byte.
    await waitFor(() => expect(harness.putLabelIds).toHaveBeenCalled());
    expect(lastSavedPlane()[STORED_AT]).toBe(5);
  });

  it("offers the canvas label under the cursor in the shared 3D pin menu", async () => {
    mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    const target = overlay();
    target.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: W, height: H, right: W, bottom: H, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
    fireEvent.contextMenu(target, { clientX: 6.5, clientY: 1.5 });
    const show3D = await screen.findByRole("button", { name: "Show 3D label 5" });
    fireEvent.click(show3D);
    expect(screen.queryByRole("button", { name: "Show 3D label 5" })).toBeNull();
  });

  it("saves a Region-only split plan outside the mask without clipping it", async () => {
    const before = storedRuns();
    const after: [number, number][] = [
      [0, STORED_AT],
      [9, 1],
      [0, H * W - STORED_AT - 1],
    ];
    harness.runSplitComponents.mockResolvedValue({
      axis: "z",
      slices: [{ index: 0, shape: [H, W], before_runs: before, runs: after }],
    });
    const { regionOnly } = mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    regionOnly(true);
    await waitFor(() => expect(harness.decodeRegionMask).toHaveBeenCalled());

    fireEvent.change(screen.getByTitle("Active label id"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Split" }));
    const splitButtons = await screen.findAllByRole("button", { name: "Split" });
    fireEvent.click(splitButtons[splitButtons.length - 1]);
    await waitFor(() => expect(harness.runSplitComponents).toHaveBeenCalled());
    await act(async () => clickSave());
    await waitFor(() => expect(harness.putLabelIds).toHaveBeenCalled());

    expect(lastSavedPlane()[STORED_AT]).toBe(9);
    // putLabelIds(..., roiOnly) — plan-produced planes intentionally bypass
    // the per-pixel ROI guard so the exact 3-D result reaches disk.
    expect(harness.putLabelIds.mock.lastCall?.[6]).toBe(false);

    const alpha = () => harness.overlayPixels?.[STORED_AT * 4 + 3] ?? -1;
    await waitFor(() => expect(alpha()).toBeGreaterThan(0));
    regionOnly(false);
    await waitFor(() => expect(alpha()).toBeGreaterThan(0));
    regionOnly(true);
    // Re-entering Region only drops the session exception and restores the
    // server's strict volume-wide membership, which excludes outside-only 9.
    await waitFor(() => expect(alpha()).toBe(0));
  });

  it("blocks brush paint over a hidden label under overwrite-all", async () => {
    const { regionOnly, overwrite } = mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });
    selectBrush();
    regionOnly(true);
    await waitFor(() => expect(harness.decodeRegionMask).toHaveBeenCalled());

    // Paint over the stored instance 5, outside the ROI, with Region only ON.
    paintPixel(1, 6);
    paintPixel(1, 7); // allowed empty staging ensures there is a plane to Save
    await act(async () => {
      await Promise.resolve();
    });

    // Label 5 never touches the ROI, so it was hidden and must remain intact.
    overwrite("overwrite_all");
    regionOnly(false);
    await act(async () => {
      clickSave();
    });
    expect(lastSavedPlane()[STORED_AT]).toBe(5);
    expect(harness.confirm).not.toHaveBeenCalled();
  });

  it("blocks erase over a hidden label", async () => {
    const { regionOnly, overwrite } = mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    regionOnly(true);
    await waitFor(() => expect(harness.decodeRegionMask).toHaveBeenCalled());
    selectErase();
    paintPixel(1, 6);
    selectBrush();
    paintPixel(1, 7); // force a Save without touching the hidden-label assertion

    overwrite("overwrite_all");
    regionOnly(false);
    await act(async () => clickSave());
    expect(lastSavedPlane()[STORED_AT]).toBe(5);
  });

  it("stages paint on empty outside voxels and Undo removes it", async () => {
    const { regionOnly, overwrite } = mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    regionOnly(true);
    await waitFor(() => expect(harness.decodeRegionMask).toHaveBeenCalled());
    selectBrush();
    paintPixel(1, 7);
    clickCanvasUndo();

    overwrite("overwrite_all");
    regionOnly(false);
    await act(async () => clickSave());
    expect(lastSavedPlane()[1 * W + 7]).toBe(0);

    // Re-entering cannot resurrect a visibility exception: the display path
    // has no exemptions and the raw pixel is back at baseline.
    regionOnly(true);
    expect(harness.confirm).not.toHaveBeenCalled();
  });

  it("never shows an outside-only fragment across Region-only toggle and Undo", async () => {
    const { regionOnly } = mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    regionOnly(true);
    await waitFor(() => expect(harness.decodeRegionMask).toHaveBeenCalled());
    selectBrush();
    paintPixel(1, 7);

    const alpha = () => harness.overlayPixels?.[(1 * W + 7) * 4 + 3] ?? -1;
    await waitFor(() => expect(alpha()).toBe(0));
    regionOnly(false);
    await waitFor(() => expect(alpha()).toBeGreaterThan(0));
    regionOnly(true);
    await waitFor(() => expect(alpha()).toBe(0));
    clickCanvasUndo();
    expect(alpha()).toBe(0);
  });

  it("presents an outside edit over an existing label as that label under empty-only", async () => {
    const { regionOnly, overwrite } = mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });
    selectBrush();
    regionOnly(true);
    await waitFor(() => expect(harness.decodeRegionMask).toHaveBeenCalled());

    paintPixel(1, 6);
    await act(async () => {
      await Promise.resolve();
    });

    overwrite("overwrite_empty");
    regionOnly(false);
    await act(async () => {
      clickSave();
    });

    // The stored instance wins the conflict; the annotator's other work stands.
    expect(lastSavedPlane()[STORED_AT]).toBe(5);
  });

  it("asks before saving with Region only on, instead of dropping outside work", async () => {
    const { regionOnly } = mount();
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });
    selectBrush();
    regionOnly(true);
    await waitFor(() => expect(harness.decodeRegionMask).toHaveBeenCalled());

    paintPixel(1, 6);
    await act(async () => {
      await Promise.resolve();
    });

    harness.confirm.mockReturnValue(false);
    await act(async () => {
      clickSave();
    });

    // Declining leaves everything pending — nothing was written, nothing lost.
    expect(harness.confirm).toHaveBeenCalledWith(
      expect.stringContaining("outside the region will not be written"),
    );
    expect(harness.putLabelIds).not.toHaveBeenCalled();
  });
});
