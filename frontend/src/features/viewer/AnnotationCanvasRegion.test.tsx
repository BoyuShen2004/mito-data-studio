import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AnnotationCanvas, { type AxisControls } from "./AnnotationCanvas";
import { clearRegionIndexCache } from "./regionIndex";

/**
 * The canvas half of the two region features: where Jump to region sits, and
 * that Region only stops clipping the label overlay once this plane's ROI is
 * decoded (from then on `computeBaseImage` filters by instance instead, which
 * is what shows a mitochondrion whole).
 */

const canvas = vi.hoisted(() => ({
  getRegionIndex: vi.fn(),
  decodeRegionMask: vi.fn(),
  fetchObjectUrl: vi.fn(),
}));

vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: { id: 4 } }) }));

vi.mock("../rendering", () => ({
  phase14ChunkRendererEnabled: () => false,
  chunkFallbackMessage: () => "",
  ChunkRenderedImageSource: class {},
}));

// Real decoding needs a 2D canvas jsdom does not provide; the mask it returns
// is the thing under test everywhere else (see regionOverlap.test.ts).
vi.mock("./regionOverlap", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./regionOverlap")>()),
  decodeRegionMask: canvas.decodeRegionMask,
}));

vi.mock("./Labels3DPanel", () => ({ default: () => <div /> }));

// Slice PNGs are fetched directly (not through the read-api adapter), and a
// real fetch here would only exercise the auth client.
vi.mock("../../api/viewer", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/viewer")>()),
  fetchObjectUrl: canvas.fetchObjectUrl,
}));

const meta = {
  shape: { z: 6, y: 8, x: 8 },
  dtype: "uint8",
  axes: ["z", "y", "x"],
  has_label: true,
  has_region_mask: true,
  volume_id: 3,
  ready_streaming: false,
  region_ready_streaming: false,
  display_range: { lo: 0, hi: 255 },
};

const api = {
  getVolumeMeta: vi.fn(async () => meta),
  getLabelState: vi.fn(async () => ({ max_label_id: 2, next_label_id: 3 })),
  getLabelsSummary: vi.fn(async () => ({
    labels: [],
    stats: { total: 0, proposed: 0, edited: 0, verified: 0 },
  })),
  // One 8x8 plane: instance 1 on the first two rows, background elsewhere.
  getLabelIds: vi.fn(async () => ({
    shape: [8, 8] as [number, number],
    runs: [
      [1, 16],
      [0, 48],
    ] as [number, number][],
  })),
  imageSlicePath: (volumeId: number, p: { axis: string; index: number }) =>
    `/image/${volumeId}/${p.axis}/${p.index}`,
  regionMaskSlicePath: (volumeId: number, p: { axis: string; index: number }) =>
    `/region/${volumeId}/${p.axis}/${p.index}`,
  getRegionIndex: canvas.getRegionIndex,
  // Volume-wide Region only membership; these fixtures exercise the
  // per-plane half, so the server half is deliberately "no ROI known".
  getRegionLabelIds: vi.fn(async () => ({ has_region: false, ids: [] })),
  fetchLabels3DMesh: vi.fn(),
};

function mount() {
  // Region only lives in the page topbar, which the canvas drives through
  // this handle — the same one `RegionOnlyButton` uses.
  let controls: AxisControls | null = null;
  const view = render(
    <AnnotationCanvas
      taskId={5}
      volumeId={3}
      zStart={0}
      zEnd={5}
      editable={false}
      api={api as never}
      onAxisControls={(next) => {
        controls = next ?? controls;
      }}
    />,
  );
  return { view, regionOnly: (on: boolean) => act(() => controls?.changeRegionOnly(on)) };
}

const overlayCanvas = () =>
  document.querySelector("canvas") as HTMLCanvasElement | null;

describe("AnnotationCanvas region features", () => {
  beforeEach(() => {
    clearRegionIndexCache();
    window.sessionStorage.clear();
    canvas.getRegionIndex.mockReset().mockResolvedValue({
      axis: "z",
      length: 2,
      indices: [4, 5],
    });
    canvas.decodeRegionMask.mockReset().mockResolvedValue(new Uint8Array(64).fill(1));
    canvas.fetchObjectUrl.mockReset().mockImplementation(async (path: string) => `blob:${path}`);
  });

  it("puts Jump to region immediately left of Fit window", async () => {
    mount();
    await screen.findByRole("button", { name: "Fit window" });

    const labels = Array.from(document.querySelectorAll("button")).map(
      (button) => button.textContent,
    );
    const jump = labels.indexOf("Jump to region");
    expect(jump).toBeGreaterThanOrEqual(0);
    expect(labels[jump + 1]).toBe("Fit window");
    expect(labels[jump + 2]).toBe("Fit width");
  });

  it("jumps the current axis to the nearest slice that has region", async () => {
    mount();
    const button = await screen.findByRole("button", { name: /jump to region/i });

    await userEvent.click(button);

    // z starts at 0; the nearest ROI-bearing plane is 4, shown 1-based.
    await waitFor(() =>
      expect(
        (document.querySelector('input[title^="Go to z layer"]') as HTMLInputElement)
          ?.value,
      ).toBe("5"),
    );
  });

  it("does not decode the region while Region only is off", async () => {
    mount();
    await screen.findByRole("button", { name: "Fit window" });

    await waitFor(() => expect(overlayCanvas()).toBeTruthy());
    expect(canvas.decodeRegionMask).not.toHaveBeenCalled();
    expect(overlayCanvas()?.style.maskImage).toBe("");
  });

  it("clips to the ROI only until the plane's region is decoded", async () => {
    const decode: { release?: (mask: Uint8Array) => void } = {};
    canvas.decodeRegionMask.mockImplementation(
      () =>
        new Promise<Uint8Array>((resolve) => {
          decode.release = resolve;
        }),
    );

    const { view, regionOnly } = mount();
    await screen.findByRole("button", { name: "Fit window" });
    regionOnly(true);

    // Before the decode lands the pixel clip is the only thing keeping labels
    // inside the region, so it must still be applied.
    await waitFor(() => expect(overlayCanvas()?.style.maskImage).toMatch(/url\(/));

    decode.release?.(new Uint8Array(64).fill(1));
    // Once instance filtering owns the decision the clip comes off, so an
    // instance that reaches into the ROI is drawn whole.
    await waitFor(() => expect(overlayCanvas()?.style.maskImage).toBe(""));
    view.unmount();
  });
});
