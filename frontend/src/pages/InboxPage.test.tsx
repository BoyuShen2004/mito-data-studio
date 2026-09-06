import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import InboxPage from "./InboxPage";

const harness = vi.hoisted(() => ({
  fetchNotifications: vi.fn(),
  markRead: vi.fn(),
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
vi.mock("../api/notifications", () => ({
  fetchNotifications: harness.fetchNotifications,
  markNotificationsRead: harness.markRead,
}));

function notification(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    verb: "task.assigned",
    title: "cortex1 assigned to you",
    body: "Project A · z 1–64",
    url: "/tasks/12",
    actor: "mgr",
    target_type: "AnnotationTask",
    target_id: "12",
    read: false,
    created_at: "2026-01-02T03:04:05Z",
    ...overrides,
  };
}

function renderInbox() {
  return render(
    <MemoryRouter>
      <InboxPage />
    </MemoryRouter>,
  );
}

describe("inbox", () => {
  beforeEach(() => {
    harness.fetchNotifications.mockReset().mockResolvedValue({
      results: [notification()],
      unread: 1,
      limit: 50,
      offset: 0,
    });
    harness.markRead.mockReset().mockResolvedValue({ marked: 1, unread: 0 });
  });

  it("lists notifications and links them to their target", async () => {
    renderInbox();
    const link = await screen.findByRole("link", {
      name: "cortex1 assigned to you",
    });
    expect(link.getAttribute("href")).toBe("/tasks/12");
  });

  it("says the feature is off rather than showing an error", async () => {
    harness.fetchNotifications.mockRejectedValue(new FakeApiError(503));
    renderInbox();
    expect(
      await screen.findByText(/Notifications are not enabled/),
    ).toBeTruthy();
  });

  it("marks one read when its link is followed", async () => {
    renderInbox();
    fireEvent.click(
      await screen.findByRole("link", { name: "cortex1 assigned to you" }),
    );
    await waitFor(() => expect(harness.markRead).toHaveBeenCalledWith([1]));
  });

  it("does not re-mark an already-read row", async () => {
    harness.fetchNotifications.mockResolvedValue({
      results: [notification({ read: true })],
      unread: 0,
      limit: 50,
      offset: 0,
    });
    renderInbox();
    fireEvent.click(
      await screen.findByRole("link", { name: "cortex1 assigned to you" }),
    );
    await waitFor(() => expect(harness.markRead).not.toHaveBeenCalled());
  });

  it("marks everything read from the header", async () => {
    renderInbox();
    fireEvent.click(await screen.findByRole("button", { name: "Mark all read" }));
    await waitFor(() => expect(harness.markRead).toHaveBeenCalledWith());
  });

  it("disables mark-all when nothing is unread", async () => {
    harness.fetchNotifications.mockResolvedValue({
      results: [notification({ read: true })],
      unread: 0,
      limit: 50,
      offset: 0,
    });
    renderInbox();
    const button = (await screen.findByRole("button", {
      name: "Mark all read",
    })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("filters to unread and back", async () => {
    renderInbox();
    fireEvent.click(await screen.findByRole("button", { name: "Unread only" }));
    await waitFor(() =>
      expect(harness.fetchNotifications).toHaveBeenLastCalledWith({
        unreadOnly: true,
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Show all" }));
    await waitFor(() =>
      expect(harness.fetchNotifications).toHaveBeenLastCalledWith({
        unreadOnly: false,
      }),
    );
  });

  it("shows an empty state rather than a blank page", async () => {
    harness.fetchNotifications.mockResolvedValue({
      results: [],
      unread: 0,
      limit: 50,
      offset: 0,
    });
    renderInbox();
    expect(await screen.findByText("Nothing here yet.")).toBeTruthy();
  });
});
