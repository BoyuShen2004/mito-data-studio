import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SubmissionQualityCard from "./SubmissionQualityCard";

const harness = vi.hoisted(() => ({ fetchQuality: vi.fn() }));
vi.mock("../api/quality", () => ({
  fetchSubmissionQuality: harness.fetchQuality,
}));

function score(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    kind: "gold_standard",
    reference_submission: 9,
    dice: 0.912,
    iou: 0.84,
    precision: 0.93,
    recall: 0.9,
    instance_f1: 0.5,
    false_merges: 3,
    false_splits: 0,
    variation_of_information: 0.4,
    provider: "overlap",
    detail: {},
    computed_at: "2026-01-02T03:04:05Z",
    ...overrides,
  };
}

describe("submission quality card", () => {
  beforeEach(() => {
    harness.fetchQuality.mockReset().mockResolvedValue([score()]);
  });

  it("renders nothing when no score exists", async () => {
    // An empty card reading "0.000 Dice" would be a false accusation about
    // work that was simply never measured.
    harness.fetchQuality.mockResolvedValue([]);
    const { container } = render(<SubmissionQualityCard submissionId={1} />);
    await waitFor(() => expect(harness.fetchQuality).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  it("shows the measured metrics", async () => {
    render(<SubmissionQualityCard submissionId={1} />);
    expect(await screen.findByText("0.912")).toBeTruthy();
    expect(screen.getByText("Dice")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
  });

  it("renders an unmeasured metric as an em-dash", async () => {
    harness.fetchQuality.mockResolvedValue([
      score({ instance_f1: null, false_splits: null }),
    ]);
    render(<SubmissionQualityCard submissionId={1} />);
    await waitFor(() => expect(screen.getAllByText("—").length).toBe(2));
  });

  it("names what the score was measured against", async () => {
    render(<SubmissionQualityCard submissionId={1} />);
    expect(
      await screen.findByText(/Scored against the gold-standard reference/),
    ).toBeTruthy();
  });
});
