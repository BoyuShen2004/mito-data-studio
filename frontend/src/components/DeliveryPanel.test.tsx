import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DeliveryPanel from "./DeliveryPanel";

const harness = vi.hoisted(() => ({
  fetchMilestones: vi.fn(),
  fetchDelivery: vi.fn(),
  createMilestone: vi.fn(),
  updateMilestone: vi.fn(),
  deleteMilestone: vi.fn(),
  fetchDetail: vi.fn(),
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
vi.mock("../api/milestones", () => ({
  fetchMilestones: harness.fetchMilestones,
  fetchProjectDelivery: harness.fetchDelivery,
  createMilestone: harness.createMilestone,
  updateMilestone: harness.updateMilestone,
  deleteMilestone: harness.deleteMilestone,
  fetchMilestoneDetail: harness.fetchDetail,
}));

const MILESTONE = {
  id: 4,
  project: 1,
  name: "Phase 1",
  description: "",
  due_on: "2026-03-01",
  order: 0,
  status: "active" as const,
  volumes: [],
  target_metric: "tasks_approved" as const,
  target_value: 10,
  completed_at: null,
  created_at: "2026-01-01T00:00:00Z",
  progress: {
    id: 4,
    name: "Phase 1",
    due_on: "2026-03-01",
    status: "active" as const,
    target_metric: "tasks_approved" as const,
    target_value: 10,
    achieved: 4,
    remaining: 6,
    percent_complete: 40,
    days_remaining: 20,
    overdue: false,
    scoped_volumes: 0,
  },
};

const DELIVERY = {
  throughput: { start: "2026-01-01", end: "2026-01-30", points: [] },
  productivity: { count: 0, results: [] },
  attention: {
    overdue: { count: 0, results: [] },
    due_soon: { count: 0, results: [] },
    stale_reviews: { count: 0, results: [] },
    thresholds: { soon_days: 3, stale_days: 3 },
  },
};

const renderPanel = () =>
  render(
    <MemoryRouter>
      <DeliveryPanel projectId={1} />
    </MemoryRouter>,
  );

// Spied once, reset per test. Re-spying inside `beforeEach` wraps the previous
// spy instead of replacing it, so call counts carry across tests and
// `toHaveBeenCalledOnce` starts failing on the second test that confirms.
const confirmSpy = vi.spyOn(window, "confirm");

describe("delivery panel milestones", () => {
  beforeEach(() => {
    confirmSpy.mockReset().mockReturnValue(true);
    harness.fetchMilestones
      .mockReset()
      .mockResolvedValue({ results: [MILESTONE], can_edit: true });
    harness.fetchDelivery.mockReset().mockResolvedValue(DELIVERY);
    harness.updateMilestone.mockReset().mockResolvedValue(MILESTONE);
    harness.deleteMilestone.mockReset().mockResolvedValue(undefined);
  });

  it("edits a milestone in place", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Target"), {
      target: { value: "25" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(harness.updateMilestone).toHaveBeenCalledWith(4, {
        name: "Phase 1",
        due_on: "2026-03-01",
        target_value: 25,
      }),
    );
  });

  it("discards an abandoned edit rather than remembering it", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Target"), {
      target: { value: "99" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect((screen.getByLabelText("Target") as HTMLInputElement).value).toBe("10");
    expect(harness.updateMilestone).not.toHaveBeenCalled();
  });

  it("asks before removing a milestone", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    await waitFor(() => expect(confirmSpy).toHaveBeenCalledOnce());
    await waitFor(() => expect(harness.deleteMilestone).toHaveBeenCalledWith(4));
  });

  it("removes nothing when the confirmation is declined", async () => {
    confirmSpy.mockReturnValue(false);
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    await waitFor(() => expect(confirmSpy).toHaveBeenCalledOnce());
    expect(harness.deleteMilestone).not.toHaveBeenCalled();
  });

  it("shows no edit controls to somebody who may not edit", async () => {
    harness.fetchMilestones.mockResolvedValue({
      results: [MILESTONE],
      can_edit: false,
    });
    renderPanel();
    await screen.findByText("Phase 1");
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
  });

  it("surfaces a save failure instead of closing the form", async () => {
    harness.updateMilestone.mockRejectedValue(new Error("due date in the past"));
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("due date in the past")).toBeTruthy();
    // Still editing, so the user's input is not lost.
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });

  it("says the feature is off rather than erroring", async () => {
    harness.fetchMilestones.mockRejectedValue(new FakeApiError(503));
    renderPanel();
    expect(
      await screen.findByText(/Milestones and delivery analytics are not enabled/),
    ).toBeTruthy();
  });
});
