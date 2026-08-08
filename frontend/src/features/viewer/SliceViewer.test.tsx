import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SliceViewer from "./SliceViewer";

const viewer = vi.hoisted(() => ({
  enabled: false,
  render: vi.fn(),
  dispose: vi.fn(),
  fetchObjectUrl: vi.fn(),
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 9 } }),
}));

vi.mock("../rendering", () => ({
  phase14ChunkRendererEnabled: () => viewer.enabled,
  chunkFallbackMessage: () => "Chunk loading failed; using the TIFF/PNG source.",
  ChunkRenderedImageSource: class {
    render = viewer.render;
    dispose = viewer.dispose;
  },
}));

vi.mock("../../api/viewer", () => ({
  getVolumeMeta: vi.fn(async () => ({
    shape: { z: 3, y: 4, x: 5 },
    dtype: "uint16",
    axes: ["z", "y", "x"],
    has_label: false,
    has_region_mask: false,
    volume_id: 7,
    ready_streaming: true,
    display_range: { lo: 0, hi: 1000 },
  })),
  fetchObjectUrl: viewer.fetchObjectUrl,
  imageSlicePath: (volumeId: number, p: { axis: string; index: number }) =>
    `/image/${volumeId}/${p.axis}/${p.index}`,
  labelSlicePath: vi.fn(),
  regionMaskSlicePath: vi.fn(),
  getRegionIndex: vi.fn(),
}));

describe("SliceViewer Phase 14 data-source selection", () => {
  beforeEach(() => {
    viewer.enabled = false;
    viewer.render.mockReset();
    viewer.dispose.mockReset();
    viewer.fetchObjectUrl.mockReset().mockResolvedValue("blob:tiff");
  });

  it("preserves the TIFF path when the Phase 14 flag is disabled", async () => {
    render(<SliceViewer volumeId={7} />);
    await waitFor(() => expect(viewer.fetchObjectUrl).toHaveBeenCalled());
    expect(viewer.render).not.toHaveBeenCalled();
    expect(screen.queryByText(/using the TIFF/)).toBeNull();
  });

  it("falls back visibly and one-way when a chunk foreground read fails", async () => {
    viewer.enabled = true;
    viewer.render.mockRejectedValue(new Error("malformed chunk"));
    render(<SliceViewer volumeId={7} />);
    await screen.findByText("Chunk loading failed; using the TIFF/PNG source.");
    expect(viewer.render.mock.calls.length).toBeGreaterThan(0);
    await waitFor(() => expect(viewer.fetchObjectUrl).toHaveBeenCalled());
  });

  it("disposes the chunk session on unmount", async () => {
    viewer.enabled = true;
    viewer.render.mockResolvedValue({
      url: "blob:chunk",
      mag: "1",
      buildIdentity: "build",
      coarse: false,
    });
    const mounted = render(<SliceViewer volumeId={7} />);
    await waitFor(() => expect(viewer.render).toHaveBeenCalled());
    mounted.unmount();
    expect(viewer.dispose).toHaveBeenCalled();
  });

  it("uses bottom arrows for layers and keyboard arrows for pan", async () => {
    const { container } = render(<SliceViewer volumeId={7} />);
    await screen.findByAltText("slice 1");
    const viewport = container.querySelector<HTMLElement>(".canvas-viewport")!;
    Object.defineProperty(viewport, "clientWidth", { configurable: true, value: 500 });
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: 500 });
    viewport.scrollLeft = 400;
    viewport.scrollTop = 400;
    const sliceInput = screen.getByTitle("Go to z layer (1–3)") as HTMLInputElement;

    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(viewport.scrollLeft).toBe(300);
    expect(sliceInput.value).toBe("2");
    expect(screen.getByAltText("slice 1")).toBeTruthy();

    fireEvent.keyDown(window, { key: "ArrowUp" });
    expect(viewport.scrollTop).toBe(300);
    expect(sliceInput.value).toBe("2");

    fireEvent.click(screen.getByTitle("Previous layer"));
    await screen.findByAltText("slice 0");
    expect(sliceInput.value).toBe("1");
    expect(viewport.scrollLeft).toBe(300);

    fireEvent.click(screen.getByTitle("Next layer"));
    await screen.findByAltText("slice 1");
    expect(sliceInput.value).toBe("2");
  });
});
