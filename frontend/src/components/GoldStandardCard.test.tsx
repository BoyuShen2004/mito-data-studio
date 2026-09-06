import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import GoldStandardCard from "./GoldStandardCard";

const harness = vi.hoisted(() => ({
  fetchApproved: vi.fn(),
  setGold: vi.fn(),
}));
const { FakeApiError } = vi.hoisted(() => ({
  FakeApiError: class extends Error {
    status: number;
    constructor(status: number) {
      super("nope");
      this.status = status;
    }
  },
}));

vi.mock("../api/client", () => ({ ApiError: FakeApiError }));
vi.mock("../api/quality", () => ({
  fetchApprovedSubmissions: harness.fetchApproved,
  setGoldStandard: harness.setGold,
}));

const APPROVED = [
  {
    id: 12,
    task: 3,
    annotator: "ann",
    source: "inapp",
    submitted_at: "2026-01-02T00:00:00Z",
  },
];

function renderCard(props: Record<string, unknown> = {}) {
  return render(
    <GoldStandardCard
      volumeId={7}
      isGoldStandard={false}
      referenceSubmission={null}
      isManager
      onChange={() => undefined}
      {...props}
    />,
  );
}

describe("gold standard card", () => {
  beforeEach(() => {
    harness.fetchApproved.mockReset().mockResolvedValue(APPROVED);
    harness.setGold.mockReset().mockResolvedValue({
      volume: 7,
      is_gold_standard: true,
      reference_submission: 12,
    });
  });

  it("is invisible to anyone who is not a manager", () => {
    // An annotator who could see it would know they were being tested, which
    // is the one thing a gold standard must not reveal.
    const { container } = renderCard({ isManager: false });
    expect(container.innerHTML).toBe("");
    expect(harness.fetchApproved).not.toHaveBeenCalled();
  });

  it("renders nothing when quality metrics are disabled", async () => {
    harness.fetchApproved.mockRejectedValue(new FakeApiError(503));
    const { container } = renderCard();
    await waitFor(() => expect(harness.fetchApproved).toHaveBeenCalled());
    await waitFor(() => expect(container.innerHTML).toBe(""));
  });

  it("explains why it cannot be enabled without an approved submission", async () => {
    harness.fetchApproved.mockResolvedValue([]);
    renderCard();
    expect(
      await screen.findByText(/No approved submission on this volume yet/),
    ).toBeTruthy();
  });

  it("will not enable without a reference selected", async () => {
    renderCard();
    const button = (await screen.findByRole("button", {
      name: "Mark as gold standard",
    })) as HTMLButtonElement;
    // Enabling with no reference is refused server-side too; disabling the
    // control means the user never has to discover that by failing.
    expect(button.disabled).toBe(true);
  });

  it("saves the selected reference", async () => {
    const onChange = vi.fn();
    renderCard({ onChange });
    const select = await screen.findByRole("combobox");
    fireEvent.change(select, { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "Mark as gold standard" }));
    await waitFor(() =>
      expect(harness.setGold).toHaveBeenCalledWith(7, {
        is_gold_standard: true,
        reference_submission: 12,
      }),
    );
    await waitFor(() => expect(onChange).toHaveBeenCalled());
  });

  it("offers a way to stop scoring once it is active", async () => {
    renderCard({ isGoldStandard: true, referenceSubmission: 12 });
    fireEvent.click(await screen.findByRole("button", { name: "Stop scoring" }));
    await waitFor(() =>
      expect(harness.setGold).toHaveBeenCalledWith(7, {
        is_gold_standard: false,
        reference_submission: 12,
      }),
    );
  });

  it("surfaces a save failure instead of claiming success", async () => {
    harness.setGold.mockRejectedValue(new Error("reference belongs elsewhere"));
    renderCard();
    fireEvent.change(await screen.findByRole("combobox"), {
      target: { value: "12" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Mark as gold standard" }));
    expect(await screen.findByText("reference belongs elsewhere")).toBeTruthy();
    expect(screen.queryByText("Saved.")).toBeNull();
  });
});
