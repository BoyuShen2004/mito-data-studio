import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AttentionPanel from "./AttentionPanel";

const harness = vi.hoisted(() => ({ fetchOverview: vi.fn() }));
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
  fetchDeliveryOverview: harness.fetchOverview,
}));

function overview(overrides: Record<string, unknown> = {}) {
  return {
    throughput: { start: "2026-01-01", end: "2026-01-30", points: [] },
    productivity: { count: 0, results: [] },
    attention: {
      overdue: { count: 0, results: [] },
      due_soon: { count: 0, results: [] },
      stale_reviews: { count: 0, results: [] },
      thresholds: { soon_days: 3, stale_days: 3 },
      ...(overrides.attention as object),
    },
  };
}

const renderPanel = () =>
  render(
    <MemoryRouter>
      <AttentionPanel />
    </MemoryRouter>,
  );

describe("cross-project attention panel", () => {
  beforeEach(() => {
    harness.fetchOverview.mockReset().mockResolvedValue(overview());
  });

  it("lists overdue work from more than one project", async () => {
    harness.fetchOverview.mockResolvedValue(
      overview({
        attention: {
          overdue: {
            count: 2,
            results: [
              { id: 1, volume: "cortex", project: "A", deadline: "2026-01-01", status: "assigned", assigned_to: "ann" },
              { id: 2, volume: "hippo", project: "B", deadline: "2026-01-02", status: "assigned", assigned_to: "bob" },
            ],
          },
          due_soon: { count: 0, results: [] },
          stale_reviews: { count: 0, results: [] },
          thresholds: { soon_days: 3, stale_days: 3 },
        },
      }),
    );
    renderPanel();
    // The whole point: two different projects in one view.
    expect(await screen.findByText("A")).toBeTruthy();
    expect(screen.getByText("B")).toBeTruthy();
  });

  it("says the feature is off rather than showing an error", async () => {
    harness.fetchOverview.mockRejectedValue(new FakeApiError(503));
    renderPanel();
    expect(
      await screen.findByText(/Delivery analytics are not enabled/),
    ).toBeTruthy();
  });

  it("renders nothing for someone who may not see every project", async () => {
    // A silently empty panel would read as "nothing is overdue" to a person
    // who simply cannot see it.
    harness.fetchOverview.mockRejectedValue(new FakeApiError(403));
    const { container } = renderPanel();
    await waitFor(() => expect(harness.fetchOverview).toHaveBeenCalled());
    await waitFor(() => expect(container.innerHTML).toBe(""));
  });

  it("says so explicitly when there is nothing to do", async () => {
    renderPanel();
    expect(
      await screen.findByText("Nothing is overdue or waiting."),
    ).toBeTruthy();
  });

  it("links a waiting submission straight to its review page", async () => {
    harness.fetchOverview.mockResolvedValue(
      overview({
        attention: {
          overdue: { count: 0, results: [] },
          due_soon: { count: 0, results: [] },
          stale_reviews: {
            count: 1,
            results: [
              { id: 12, task: 3, volume: "cortex", annotator: "ann", submitted_at: "2026-01-02T00:00:00Z" },
            ],
          },
          thresholds: { soon_days: 3, stale_days: 3 },
        },
      }),
    );
    renderPanel();
    const link = await screen.findByRole("link", { name: "Review" });
    expect(link.getAttribute("href")).toBe("/submissions/12/review");
  });
});
