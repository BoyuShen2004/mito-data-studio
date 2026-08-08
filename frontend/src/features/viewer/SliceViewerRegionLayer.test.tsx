import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SliceViewer from "./SliceViewer";

/**
 * View with a region mask: when the ROI derivative is ready the overlay comes
 * from chunks, and when it is not — or when it fails mid-session — the
 * full-slice PNG path still serves it. The image layer's transport is
 * independent of both.
 */

const viewer = vi.hoisted(() => ({
  enabled: true,
  meta: {} as Record<string, unknown>,
  constructed: [] as Array<{ layer?: string }>,
  renderImage: vi.fn(),
  renderRegion: vi.fn(),
  dispose: vi.fn(),
  fetchObjectUrl: vi.fn(),
  regionSlicePath: vi.fn(),
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 9 } }),
}));

vi.mock("../rendering", () => ({
  phase14ChunkRendererEnabled: () => viewer.enabled,
  chunkFallbackMessage: (_error: unknown, layer = "image") =>
    layer === "region"
      ? "The region mask is not streaming yet; using the full-slice source."
      : "Chunk loading failed; using the TIFF/PNG source.",
  ChunkRenderedImageSource: class {
    layer?: string;
    constructor(options: { layer?: string }) {
      this.layer = options.layer;
      viewer.constructed.push({ layer: options.layer });
    }
    render = (request: unknown) =>
      this.layer === "region"
        ? viewer.renderRegion(request)
        : viewer.renderImage(request);
    dispose = viewer.dispose;
  },
}));

vi.mock("../../api/viewer", () => ({
  getVolumeMeta: vi.fn(async () => viewer.meta),
  fetchObjectUrl: viewer.fetchObjectUrl,
  imageSlicePath: (volumeId: number, p: { axis: string; index: number }) =>
    `/image/${volumeId}/${p.axis}/${p.index}`,
  labelSlicePath: vi.fn(),
  regionMaskSlicePath: viewer.regionSlicePath,
  getRegionIndex: vi.fn(async () => ({ axis: "z", length: 0, indices: [] })),
}));

const BASE_META = {
  shape: { z: 3, y: 4, x: 5 },
  dtype: "uint16",
  axes: ["z", "y", "x"],
  has_label: false,
  has_region_mask: true,
  volume_id: 7,
  ready_streaming: true,
  display_range: { lo: 0, hi: 1000 },
};

const frame = (url: string) => ({
  url,
  mag: "1",
  buildIdentity: "build",
  coarse: false,
});

describe("SliceViewer region-mask transport", () => {
  beforeEach(() => {
    viewer.enabled = true;
    viewer.constructed.length = 0;
    viewer.meta = { ...BASE_META, region_ready_streaming: true };
    viewer.renderImage.mockReset().mockResolvedValue(frame("blob:image"));
    viewer.renderRegion.mockReset().mockResolvedValue(frame("blob:region"));
    viewer.dispose.mockReset();
    viewer.fetchObjectUrl.mockReset().mockResolvedValue("blob:slice");
    viewer.regionSlicePath.mockReset().mockImplementation(
      (volumeId: number, axis: string, index: number) =>
        `/region/${volumeId}/${axis}/${index}`,
    );
  });

  it("streams the ROI through chunks instead of the per-slice PNG", async () => {
    render(<SliceViewer volumeId={7} />);
    await waitFor(() => expect(viewer.renderRegion).toHaveBeenCalled());

    expect(viewer.constructed.map((entry) => entry.layer)).toEqual([
      undefined,
      "region",
    ]);
    expect(viewer.regionSlicePath).not.toHaveBeenCalled();
  });

  it("keeps the full-slice path when the ROI derivative is not ready", async () => {
    viewer.meta = { ...BASE_META, region_ready_streaming: false };
    render(<SliceViewer volumeId={7} />);

    await waitFor(() => expect(viewer.regionSlicePath).toHaveBeenCalled());
    expect(viewer.renderRegion).not.toHaveBeenCalled();
    // …while the image layer still streams.
    expect(viewer.renderImage).toHaveBeenCalled();
    expect(viewer.constructed.map((entry) => entry.layer)).toEqual([undefined]);
  });

  it("falls back for the ROI alone when its chunk read fails", async () => {
    viewer.renderRegion.mockRejectedValue(new Error("region chunks gone"));
    render(<SliceViewer volumeId={7} />);

    await screen.findByText(
      "The region mask is not streaming yet; using the full-slice source.",
    );
    await waitFor(() => expect(viewer.regionSlicePath).toHaveBeenCalled());
    // The image never left the chunk path.
    expect(viewer.renderImage).toHaveBeenCalled();
  });

  it("mounts no region source for a volume without a mask", async () => {
    viewer.meta = {
      ...BASE_META,
      has_region_mask: false,
      region_ready_streaming: false,
    };
    render(<SliceViewer volumeId={7} />);

    await waitFor(() => expect(viewer.renderImage).toHaveBeenCalled());
    expect(viewer.constructed.map((entry) => entry.layer)).toEqual([undefined]);
    expect(viewer.renderRegion).not.toHaveBeenCalled();
    expect(viewer.regionSlicePath).not.toHaveBeenCalled();
  });
});
