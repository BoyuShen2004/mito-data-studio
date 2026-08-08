import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AnnotationCanvas, { type AxisControls } from "./AnnotationCanvas";

/**
 * Opening a hard case (`/hard-cases/:id`) or its public share
 * (`/share/hard-case/:token`) must land the recipient on a layer where the
 * flagged label is actually painted.
 *
 * Both pages pass `initialSoloId`/`initialActiveId`, but the opening layer
 * comes from the *task's* `z_start` — so a label recorded at z 243 opened on
 * layer 1, where solo (correctly) paints nothing. The canvas looked empty
 * while the 3D panel showed the mesh, and the only escape was clearing the
 * visibility filter by hand.
 */

vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: { id: 4 } }) }));

vi.mock("../rendering", () => ({
  phase14ChunkRendererEnabled: () => false,
  chunkFallbackMessage: () => "",
  ChunkRenderedImageSource: class {},
}));

vi.mock("./Labels3DPanel", () => ({ default: () => <div /> }));

const hoisted = vi.hoisted(() => ({ fetchObjectUrl: vi.fn() }));

vi.mock("../../api/viewer", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/viewer")>()),
  fetchObjectUrl: hoisted.fetchObjectUrl,
  getTrackingPrompts: vi.fn(async () => ({ version: 1, items: [], pending_review: null })),
}));

/** A tall volume, like the one the bug was found on: the task starts at z 0
 * and the recorded label lives near the far end. */
const meta = {
  shape: { z: 300, y: 4, x: 4 },
  dtype: "uint8",
  axes: ["z", "y", "x"],
  has_label: true,
  has_region_mask: true,
  volume_id: 3,
  ready_streaming: false,
  region_ready_streaming: false,
  display_range: { lo: 0, hi: 255 },
};

const plane = { shape: [4, 4] as [number, number], runs: [[6, 16]] as [number, number][] };

const api = {
  getVolumeMeta: vi.fn(async () => meta),
  getLabelState: vi.fn(async () => ({ max_label_id: 6, next_label_id: 7 })),
  getLabelsSummary: vi.fn(async () => ({
    labels: [
      {
        id: 6,
        voxel_count: 640,
        z_start: 242,
        z_end: 255,
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
  getRegionLabelIds: vi.fn(async () => ({ has_region: true, ids: [6] })),
  fetchLabels3DMesh: vi.fn(),
};

const layerInput = () =>
  document.querySelector('input[title^="Go to z layer"]') as HTMLInputElement;

/** Mount the way `HardCaseDetailPage` / `HardCaseSharePage` do: the task's
 * z range, view-only, soloed + active on the recorded label. */
function mountHardCase(onAxisControls?: (c: AxisControls | null) => void) {
  return render(
    <AnnotationCanvas
      taskId={5}
      volumeId={3}
      zStart={0}
      zEnd={299}
      editable={false}
      api={api as never}
      initialActiveId={6}
      initialSoloId={6}
      onAxisControls={onAxisControls}
    />,
  );
}

describe("hard case open focuses the shared label's layer", () => {
  beforeEach(() => {
    hoisted.fetchObjectUrl.mockReset().mockImplementation(async (path: string) => `blob:${path}`);
    api.getLabelIds.mockClear();
    api.getRegionLabelIds.mockClear();
  });

  it("jumps to the layer where the soloed label starts", async () => {
    mountHardCase();
    await screen.findByRole("button", { name: "Fit window" });

    // z_start 242 is layer 243 to a human (`layerIndex.displayLayer`), not the
    // task's layer 1.
    await waitFor(() => expect(layerInput().value).toBe("243"));
    // View-only has no Active id box, so the Labels row is the visible proof
    // the shared label is the selected one.
    const active = document.querySelector(".labels-row-active")!;
    expect(active.textContent?.trim().startsWith("6")).toBe(true);
  });

  it("keeps the shared label soloed after the jump and offers Show all", async () => {
    mountHardCase();
    await waitFor(() => expect(layerInput().value).toBe("243"));

    // Solo survives the navigation — the jump exists to make solo *visible*,
    // not to replace it.
    expect(screen.getByTitle("Un-solo")).toBeTruthy();
    // The one control that clears it is named for what it does.
    expect(screen.getByRole("button", { name: "Show all" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reset" })).toBeNull();
  });

  it("leaves Region only wired once the case has opened", async () => {
    const published: AxisControls[] = [];
    mountHardCase((c) => {
      if (c) published.push(c);
    });
    await waitFor(() => expect(layerInput().value).toBe("243"));

    const controls = published[published.length - 1];
    expect(controls.hasRegion).toBe(true);
    expect(controls.regionOnly).toBe(false);
    act(() => controls.changeRegionOnly(true));

    await waitFor(() =>
      expect(published[published.length - 1].regionOnly).toBe(true),
    );
    // Turning it on is what fetches volume-wide ROI membership.
    await waitFor(() => expect(api.getRegionLabelIds).toHaveBeenCalledWith(5));
    expect(layerInput().value).toBe("243");
  });

  it("does not move the layer for an ordinary Annotate mount", async () => {
    render(
      <AnnotationCanvas
        taskId={5}
        volumeId={3}
        zStart={0}
        zEnd={299}
        editable
        api={api as never}
      />,
    );
    await screen.findByRole("button", { name: "Fit window" });
    await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());

    expect(layerInput().value).toBe("1");
  });
});
