import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AnnotationCanvas from "./AnnotationCanvas";

/**
 * Track propagate -> Confirm / Reject.
 *
 * The regression this pins: `/tasks/<id>/track/batch/` *plans* (it returns
 * planes for the pending buffer and never writes labels or a server-side
 * pending review), but the rail still waited for a server `pending_review`
 * before enabling its review actions. A finished propagate therefore left both
 * buttons permanently disabled, and there was no way to accept or undo the
 * result from the rail at all.
 */

const track = vi.hoisted(() => ({
  getTrackingPrompts: vi.fn(),
  replaceTrackingPrompts: vi.fn(),
  trackTaskBatch: vi.fn(),
  reviewTrackingPreview: vi.fn(),
  putTrackingPrompt: vi.fn(),
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

/** One 4x4 plane, all background — what the propagation paints over. */
const emptyPlane = { shape: [4, 4] as [number, number], runs: [[0, 16]] as [number, number][] };
/** The propagated result: instance 9 across the top row. */
const trackedRuns = [[9, 4], [0, 12]] as [number, number][];

const queuedPrompt = {
  parent_id: 9,
  subclasses: [{ index: 1, seeds: [{ z: 0, rle: [[0, 4]] as [number, number][], shape: [4, 4] as [number, number] }] }],
  // The queue always carries the annotator's explicit inclusive bounds; here
  // they are the single layer this 4-layer fixture propagates over.
  start_z: 0,
  end_z: 0,
  z_range: [0, 0] as [number, number],
  status: "ready" as const,
};

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
  // Volume-wide Region only membership; these fixtures exercise the
  // per-plane half, so the server half is deliberately "no ROI known".
  getRegionLabelIds: vi.fn(async () => ({ has_region: false, ids: [] })),
  fetchLabels3DMesh: vi.fn(),
};

function mount() {
  return render(
    <AnnotationCanvas
      taskId={5}
      volumeId={3}
      zStart={0}
      zEnd={3}
      editable
      api={api as never}
    />,
  );
}

const confirmButton = () => screen.getByRole("button", { name: "Confirm" }) as HTMLButtonElement;
const rejectButton = () => screen.getByRole("button", { name: "Reject" }) as HTMLButtonElement;
const propagateButton = () =>
  screen.getByRole("button", { name: "Propagate selected" }) as HTMLButtonElement;

/** Wait for the first plane *and* the queue fetch to land, then propagate.
 * Propagating before the canvas holds a plane makes the returned plan look
 * stale, which is a test-harness race, not the behaviour under test. */
async function propagateQueuedParent() {
  await screen.findByRole("button", { name: "Fit window" });
  await waitFor(() => expect(api.getLabelIds).toHaveBeenCalled());
  await screen.findByText("Parent 9");
  await waitFor(() => expect(propagateButton().disabled).toBe(false));
  await userEvent.click(propagateButton());
  await waitFor(() => expect(confirmButton().disabled).toBe(false));
}

describe("Track propagate review", () => {
  beforeEach(() => {
    for (const fn of Object.values(track)) fn.mockReset();
    api.getLabelIds.mockClear();
    track.fetchObjectUrl.mockImplementation(async (path: string) => `blob:${path}`);
    track.getTrackingPrompts.mockResolvedValue({
      version: 1,
      items: [queuedPrompt],
      pending_review: null,
    });
    track.trackTaskBatch.mockResolvedValue({
      results: [],
      done: 1,
      total: 1,
      axis: "z",
      slices: [{ index: 0, shape: [4, 4], runs: trackedRuns }],
    });
    track.replaceTrackingPrompts.mockImplementation(async (_id: number, items: unknown[]) => ({
      version: 1,
      items,
    }));
  });

  it("enables Confirm/Reject once a propagate has produced a result", async () => {
    mount();
    await screen.findByRole("button", { name: "Fit window" });
    expect(confirmButton().disabled).toBe(true);

    await propagateQueuedParent();

    expect(rejectButton().disabled).toBe(false);
    // Propagating again is what must be blocked while a result is unreviewed.
    expect(propagateButton().disabled).toBe(true);
  });

  it("retires the propagated parent on Confirm without a server review call", async () => {
    mount();
    await propagateQueuedParent();

    await userEvent.click(confirmButton());

    await waitFor(() => expect(track.replaceTrackingPrompts).toHaveBeenCalled());
    // Confirmed parents leave the queue; nothing is asked of the server-side
    // review endpoint, which has no pending preview to act on.
    expect(track.replaceTrackingPrompts.mock.calls[0][1]).toEqual([]);
    expect(track.reviewTrackingPreview).not.toHaveBeenCalled();
    await waitFor(() => expect(confirmButton().disabled).toBe(true));
  });

  it("restores the pre-propagation planes on Reject and re-arms the parent", async () => {
    mount();
    await propagateQueuedParent();
    // The plan landed in the pending buffer, so the editor has unsaved work.
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();

    await userEvent.click(rejectButton());

    await waitFor(() => expect(track.replaceTrackingPrompts).toHaveBeenCalled());
    const restored = track.replaceTrackingPrompts.mock.calls[0][1] as typeof queuedPrompt[];
    expect(restored).toHaveLength(1);
    // Still seeded, so it can be propagated again straight away.
    expect(restored[0].status).toBe("ready");
    expect(track.reviewTrackingPreview).not.toHaveBeenCalled();
    await waitFor(() => expect(propagateButton().disabled).toBe(false));
  });
});
