import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SliceViewer from "./SliceViewer";
import { clearRegionIndexCache } from "./regionIndex";

/**
 * Region only, on the volume viewer: labels come back as a colorized PNG, so
 * "show the whole mitochondrion that touches the region" has to be decided
 * server-side (`region_only=1`). What this asserts is that the viewer asks for
 * that render and then stops clipping the overlay to the ROI — the clip is
 * what used to cut instances off at the region boundary.
 */

const viewer = vi.hoisted(() => ({
  fetchObjectUrl: vi.fn(),
  labelSlicePath: vi.fn(),
  getRegionIndex: vi.fn(),
}));

vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: { id: 9 } }) }));

vi.mock("../rendering", () => ({
  phase14ChunkRendererEnabled: () => false,
  chunkFallbackMessage: () => "",
  ChunkRenderedImageSource: class {},
}));

vi.mock("../../api/viewer", () => ({
  getVolumeMeta: vi.fn(async () => ({
    shape: { z: 3, y: 4, x: 5 },
    dtype: "uint8",
    axes: ["z", "y", "x"],
    has_label: true,
    has_region_mask: true,
    volume_id: 7,
    ready_streaming: false,
    region_ready_streaming: false,
    display_range: { lo: 0, hi: 255 },
  })),
  fetchObjectUrl: viewer.fetchObjectUrl,
  imageSlicePath: (volumeId: number, p: { axis: string; index: number }) =>
    `/image/${volumeId}/${p.axis}/${p.index}`,
  labelSlicePath: viewer.labelSlicePath,
  regionMaskSlicePath: (volumeId: number, axis: string, index: number) =>
    `/region/${volumeId}/${axis}/${index}`,
  getRegionIndex: viewer.getRegionIndex,
}));

describe("SliceViewer Region only", () => {
  beforeEach(() => {
    clearRegionIndexCache();
    window.sessionStorage.clear();
    viewer.fetchObjectUrl.mockReset().mockImplementation(async (path: string) => `blob:${path}`);
    viewer.labelSlicePath
      .mockReset()
      .mockImplementation(
        (volumeId: number, axis: string, index: number, regionOnly?: boolean) =>
          `/label/${volumeId}/${axis}/${index}${regionOnly ? "?region_only=1" : ""}`,
      );
    viewer.getRegionIndex
      .mockReset()
      .mockResolvedValue({ axis: "z", length: 1, indices: [1] });
  });

  const labelLayer = () => screen.getByAltText("labels") as HTMLImageElement;

  it("clips nothing and asks for no filtered render while it is off", async () => {
    render(<SliceViewer volumeId={7} />);

    await waitFor(() => expect(labelLayer()).toBeTruthy());
    expect(labelLayer().style.maskImage).toBe("");
    expect(viewer.labelSlicePath).toHaveBeenCalledWith(7, "z", expect.any(Number), false);
  });

  it("asks for the whole-instance render and drops the clip when it is on", async () => {
    render(<SliceViewer volumeId={7} />);
    await waitFor(() => expect(labelLayer()).toBeTruthy());

    fireEvent.click(screen.getByLabelText("Only inside region mask"));

    await waitFor(() =>
      expect(viewer.labelSlicePath).toHaveBeenCalledWith(7, "z", expect.any(Number), true),
    );
    // The instance-level filter has taken over, so the pixel clip must be off:
    // leaving it on would re-crop the very instances the filter kept.
    await waitFor(() => expect(labelLayer().style.maskImage).toBe(""));
  });

  it("offers Jump to region immediately left of Fit window", async () => {
    render(<SliceViewer volumeId={7} />);
    await waitFor(() => expect(labelLayer()).toBeTruthy());

    const labels = Array.from(document.querySelectorAll("button")).map(
      (button) => button.textContent,
    );
    const jump = labels.indexOf("Jump to region");
    expect(jump).toBeGreaterThanOrEqual(0);
    expect(labels[jump + 1]).toBe("Fit window");
    expect(labels[jump + 2]).toBe("Fit width");
  });
});
