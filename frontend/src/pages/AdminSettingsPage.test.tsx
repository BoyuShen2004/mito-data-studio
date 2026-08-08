import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AdminSettingsPage from "./AdminSettingsPage";

const harness = vi.hoisted(() => ({ superuser: true, status: vi.fn() }));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { username: "admin", is_superuser: harness.superuser } }),
}));
vi.mock("../api/adminReset", () => ({
  getResetStatus: harness.status,
  requestResetConfirmation: vi.fn(),
  executeReset: vi.fn(),
}));

describe("protected clear-all control", () => {
  beforeEach(() => {
    harness.superuser = true;
    harness.status.mockReset().mockResolvedValue({
      phrase: "CLEAR ALL APPLICATION DATA", maintenance: false,
      backup: { valid: false, reason: "missing" }, clear: { projects: 2 },
      retain: ["external source/reference bytes"], storage: [],
      identity: { fingerprint: "abc", checkout: "/release", data_root: "/data", database: { name: "db" }, service: { release: "v1.1.1" } },
    });
  });

  it("does not expose the control to a normal user", () => {
    harness.superuser = false;
    render(<MemoryRouter><AdminSettingsPage /></MemoryRouter>);
    expect(screen.queryByRole("button", { name: "Clear all existing files" })).toBeNull();
  });

  it("shows exact scope but disables execution until maintenance and backup gates pass", async () => {
    render(<MemoryRouter><AdminSettingsPage /></MemoryRouter>);
    const button = await screen.findByRole("button", { name: "Clear all existing files" });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/External source\/reference files are unregistered but never deleted/)).not.toBeNull();
    expect(screen.getByText(/Maintenance\/write-freeze mode is not active/)).not.toBeNull();
  });
});
