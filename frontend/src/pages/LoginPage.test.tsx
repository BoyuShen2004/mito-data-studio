import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LoginPage from "./LoginPage";

const harness = vi.hoisted(() => ({
  login: vi.fn(),
  user: null as null | { role: string; is_superuser: boolean },
  fetchAccounts: vi.fn(),
  getResetStatus: vi.fn(),
  clearDevelopmentData: vi.fn(),
  getRelease: vi.fn(),
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: harness.user, login: harness.login }),
}));

vi.mock("../api/auth", () => ({
  fetchMockAccounts: harness.fetchAccounts,
  getDevelopmentResetStatus: harness.getResetStatus,
  clearDevelopmentData: harness.clearDevelopmentData,
}));

vi.mock("../api/deployment", () => ({
  getDeploymentRelease: harness.getRelease,
}));

describe("development account login helper", () => {
  beforeEach(() => {
    harness.login.mockReset();
    harness.user = null;
    harness.fetchAccounts.mockReset().mockResolvedValue([
      { username: "alice", role: "annotator", password: "demo-password" },
      { username: "requester1", role: "requester", password: "demo-password" },
    ]);
    harness.getRelease.mockReset().mockResolvedValue("mito-data-agent-v1.1.1");
    harness.getResetStatus.mockReset().mockResolvedValue({
      enabled: true,
      confirmation: "CLEAR ALL DEVELOPMENT DATA",
      clear: { projects: 2, datasets: 3, users_to_preserve: 7 },
    });
    harness.clearDevelopmentData.mockReset().mockResolvedValue({ after: {} });
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("shows the configured deployment release discreetly", async () => {
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    const version = await screen.findByLabelText("Release version");
    expect(version.textContent).toBe("1.1.1");
    expect(version.className).toContain("login-release");
  });

  it("appears below registration and only fills the normal login form", async () => {
    const { container } = render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    await screen.findByText("Development accounts");
    const registration = container.querySelector(".login-hint");
    const helper = container.querySelector(".dev-accounts");
    expect(registration).not.toBeNull();
    expect(helper).not.toBeNull();
    expect(
      registration!.compareDocumentPosition(helper!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /alice annotator/i }));
    expect((screen.getByLabelText("Username") as HTMLInputElement).value).toBe("alice");
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe(
      "demo-password",
    );
    expect(
      screen.getByRole("tab", { name: "Annotator Login" }).getAttribute(
        "aria-selected",
      ),
    ).toBe("true");
    expect(harness.login).not.toHaveBeenCalled();
  });

  it("clears after one confirmation without changing or signing in the account form", async () => {
    const { container } = render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    const clear = await screen.findByRole("button", {
      name: "Clear all existing files",
    });
    const helper = container.querySelector(".dev-accounts");
    expect(helper?.contains(clear)).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /alice annotator/i }));
    fireEvent.click(clear);
    expect((screen.getByLabelText("Username") as HTMLInputElement).value).toBe(
      "alice",
    );
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe(
      "demo-password",
    );
    await waitFor(() => expect(window.confirm).toHaveBeenCalledOnce());
    await waitFor(() => expect(harness.clearDevelopmentData).toHaveBeenCalledWith(
      "CLEAR ALL DEVELOPMENT DATA",
    ));
    expect(await screen.findByText("All development data and files were cleared.")).toBeTruthy();
    expect(harness.login).not.toHaveBeenCalled();
  });

  it("does nothing when the user cancels the confirmation", async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "Clear all existing files" }));
    await waitFor(() => expect(window.confirm).toHaveBeenCalledOnce());
    expect(harness.clearDevelopmentData).not.toHaveBeenCalled();
  });
});
