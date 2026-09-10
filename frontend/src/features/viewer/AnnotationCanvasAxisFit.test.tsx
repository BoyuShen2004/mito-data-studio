import { act, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AnnotationCanvas, { type AxisControls } from "./AnnotationCanvas";

/**
 * Switching the view axis changes the plane's geometry — z gives (y, x), y
 * gives (z, x), x gives (z, y). The fit baseline is frozen across zoom, and
 * used to stay frozen across that switch too: the new plane was stretched into
 * the previous plane's box, so a 6x8x8 volume kept looking square on every
 * axis (and an anisotropic one looked badly distorted).
 */

vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: { id: 4 } }) }));

vi.mock("../rendering", () => ({
  phase14ChunkRendererEnabled: () => false,
  ChunkRenderedImageSource: class {},
}));

vi.mock("./Labels3DPanel", () => ({ default: () => <div /> }));

vi.mock("../../api/viewer", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/viewer")>()),
  fetchObjectUrl: vi.fn(async (path: string) => `blob:${path}`),
}));

// Anisotropic on purpose: each axis has its own aspect ratio.
const meta = {
  shape: { z: 6, y: 8, x: 8 },
  dtype: "uint8",
  axes: ["z", "y", "x"],
  has_label: true,
  has_region_mask: false,
  volume_id: 3,
  ready_streaming: false,
  region_ready_streaming: false,
  display_range: { lo: 0, hi: 255 },
};

/** Plane size the server would return for `axis`, as [height, width]. */
const planeShape = (axis: string): [number, number] =>
  axis === "z" ? [meta.shape.y, meta.shape.x]
  : axis === "y" ? [meta.shape.z, meta.shape.x]
  : [meta.shape.z, meta.shape.y];

const api = {
  getVolumeMeta: vi.fn(async () => meta),
  getLabelState: vi.fn(async () => ({ max_label_id: 0, next_label_id: 1 })),
  getLabelsSummary: vi.fn(async () => ({
    labels: [],
    stats: { total: 0, proposed: 0, edited: 0, verified: 0 },
  })),
  getLabelIds: vi.fn(async (_v: number, p: { axis: string }) => {
    const [h, w] = planeShape(p.axis);
    return { shape: [h, w] as [number, number], runs: [[0, h * w]] as [number, number][] };
  }),
  imageSlicePath: (volumeId: number, p: { axis: string; index: number }) =>
    `/image/${volumeId}/${p.axis}/${p.index}`,
  regionMaskSlicePath: (volumeId: number, p: { axis: string; index: number }) =>
    `/region/${volumeId}/${p.axis}/${p.index}`,
  getRegionIndex: vi.fn(async () => ({ axis: "z", length: 0, indices: [] })),
  getRegionLabelIds: vi.fn(async () => ({ has_region: false, ids: [] })),
  fetchLabels3DMesh: vi.fn(),
};

const SHELL = 400;
const descriptors: { proto: object; prop: string; value: PropertyDescriptor | undefined }[] = [];

function stub(proto: object, prop: string, getter: (this: Element) => number) {
  descriptors.push({ proto, prop, value: Object.getOwnPropertyDescriptor(proto, prop) });
  Object.defineProperty(proto, prop, { configurable: true, get: getter });
}

const stageImage = () =>
  document.querySelector(".canvas-stage img") as HTMLImageElement;

const stage = () => document.querySelector(".canvas-stage") as HTMLElement;

describe("AnnotationCanvas view-axis fit", () => {
  beforeEach(() => {
    // jsdom reports 0 for both, which makes the fit code bail out entirely.
    stub(HTMLElement.prototype, "clientWidth", () => SHELL);
    stub(HTMLElement.prototype, "clientHeight", () => SHELL);
    // …and never decodes an image, so natural size comes from the slice path.
    const naturalFor = (src: string, dimension: 0 | 1) => {
      const axis = /\/image\/\d+\/([zyx])\//.exec(src)?.[1];
      return axis ? planeShape(axis)[dimension] : 0;
    };
    stub(HTMLImageElement.prototype, "naturalHeight", function () {
      return naturalFor((this as HTMLImageElement).src, 0);
    });
    stub(HTMLImageElement.prototype, "naturalWidth", function () {
      return naturalFor((this as HTMLImageElement).src, 1);
    });
  });

  afterEach(() => {
    while (descriptors.length) {
      const entry = descriptors.pop()!;
      if (entry.value) Object.defineProperty(entry.proto, entry.prop, entry.value);
      else delete (entry.proto as Record<string, unknown>)[entry.prop];
    }
  });

  it("refits the stage to the new plane instead of keeping the old box", async () => {
    let controls: AxisControls | null = null;
    render(
      <AnnotationCanvas
        taskId={5}
        volumeId={3}
        zStart={0}
        zEnd={5}
        mode="view"
        editable={false}
        api={api as never}
        onAxisControls={(next) => {
          controls = next ?? controls;
        }}
      />,
    );

    await waitFor(() => expect(stageImage()?.src).toContain("/image/3/z/"));
    act(() => { fireEvent.load(stageImage()); });
    // Axial plane is 8x8 — square, so it fits the square shell exactly.
    await waitFor(() => expect(stage().style.width).toBe("400px"));
    expect(stage().style.height).toBe("400px");

    // A plain wheel counts as the user taking over the view: from here the fit
    // baseline is frozen, which is exactly the state the bug lived in.
    act(() => {
      fireEvent.wheel(document.querySelector(".canvas-viewport")!, { deltaY: 40 });
    });

    act(() => { controls?.changeAxis("y"); });
    await waitFor(() => expect(stageImage()?.src).toContain("/image/3/y/"));
    act(() => { fireEvent.load(stageImage()); });

    // Coronal plane is 6 (z) x 8 (x): same width, three quarters the height.
    await waitFor(() => expect(stage().style.height).toBe("300px"));
    expect(stage().style.width).toBe("400px");
  });
});
