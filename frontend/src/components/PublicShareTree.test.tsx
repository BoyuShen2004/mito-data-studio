import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PublicShareTree from "./PublicShareTree";

const api = vi.hoisted(() => ({
  getPublicShareTree: vi.fn(),
  createPublicShare: vi.fn(),
  revokePublicShare: vi.fn(),
}));

vi.mock("../api/shares", () => api);

const volumeShare = {
  id: 33, token: "token", scope: "volume", project_id: 1,
  project_title: "Mito project", dataset_id: 2, dataset_name: "Dataset A",
  volume_id: 3, volume_name: "Volume A", created_at: "2026-08-03T00:00:00Z",
  revoked_at: null, created_by: 9, created_by_username: "annotator", url: "/share/public/token",
};

describe("PublicShareTree", () => {
  beforeEach(() => {
    api.createPublicShare.mockReset();
    api.revokePublicShare.mockReset().mockResolvedValue({});
    api.getPublicShareTree.mockReset().mockResolvedValue({
      stop_policy: "direct_scope_only",
      projects: [{
        id: 1, title: "Mito project", state: "partial", direct_shares: [],
        ungrouped_volumes: [],
        datasets: [{
          id: 2, name: "Dataset A", state: "all", direct_shares: [],
          volumes: [{id: 3, name: "Volume A", shared: true, direct_shares: [volumeShare]}],
        }],
      }],
    });
  });

  it("drills from aggregate project and dataset LEDs to a two-state volume LED", async () => {
    render(<PublicShareTree />);
    await screen.findByText("Mito project");
    expect(screen.getByLabelText("Partially shared")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", {name: "▸"}));
    expect(await screen.findByText("Dataset A")).toBeTruthy();
    expect(screen.getByLabelText("All shared")).toBeTruthy();

    const toggles = screen.getAllByRole("button", {name: "▸"});
    fireEvent.click(toggles[toggles.length - 1]);
    expect(await screen.findByText("Volume A")).toBeTruthy();
    expect(screen.getByLabelText("Shared")).toBeTruthy();
  });

  it("lets a manager stop an annotator-created volume share", async () => {
    render(<PublicShareTree />);
    await screen.findByText("Mito project");
    fireEvent.click(screen.getByRole("button", {name: "▸"}));
    const toggles = await screen.findAllByRole("button", {name: "▸"});
    fireEvent.click(toggles[toggles.length - 1]);
    fireEvent.click(await screen.findByRole("button", {name: "Stop"}));
    await waitFor(() => expect(api.revokePublicShare).toHaveBeenCalledWith(33));
    expect(await screen.findByText(/Volume share stopped/i)).toBeTruthy();
    expect(api.getPublicShareTree).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Volume A")).toBeTruthy();
    expect(screen.getAllByLabelText("Not shared").length).toBeGreaterThan(0);
  });
});
