import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ShareControl from "./ShareControl";

const api = vi.hoisted(() => ({
  getEntityShare: vi.fn(),
  createPublicShare: vi.fn(),
  revokePublicShare: vi.fn(),
}));
const auth = vi.hoisted(() => ({isManager: false}));
vi.mock("../api/shares", () => api);
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({user: {id: 4}, isManager: auth.isManager}),
}));

describe("ShareControl", () => {
  beforeEach(() => {
    vi.useRealTimers();
    auth.isManager = false;
    api.getEntityShare.mockReset().mockResolvedValue({scope: "volume", active: false, aggregate_state: "not_shared", shares: []});
    api.createPublicShare.mockReset().mockResolvedValue({id: 8, scope: "volume", url: "/share/public/token", created_by: 4});
    api.revokePublicShare.mockReset().mockResolvedValue({});
    Object.assign(navigator, {clipboard: {writeText: vi.fn().mockResolvedValue(undefined)}});
  });

  it("patches its LED and button locally after sharing", async () => {
    render(<ShareControl scope="volume" projectId={1} volumeId={3} getViewLocation={() => ({z: 7, y: 11, x: 13, axis: "y", label: 5})}/>);
    fireEvent.click(await screen.findByRole("button", {name: "Share"}));
    expect(await screen.findByLabelText("Sharing on")).toBeTruthy();
    expect(screen.getByRole("button", {name: "Stop sharing"})).toBeTruthy();
    expect(api.getEntityShare).toHaveBeenCalledTimes(1);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      "http://localhost:3000/share/public/token?z=7&y=11&x=13&axis=y&label=5",
    );
    expect(screen.queryByText(/link copied/i)).toBeNull();
  });

  it("hides Stop from an annotator when a manager owns the live share", async () => {
    api.getEntityShare.mockResolvedValue({scope: "volume", active: true, aggregate_state: "shared", shares: [{id: 9, scope: "volume", url: "/share/public/manager", created_by: 2}]});
    render(<ShareControl scope="volume" projectId={1} volumeId={3}/>);

    expect(await screen.findByRole("button", {name: "Copy link"})).toBeTruthy();
    expect(screen.queryByRole("button", {name: "Stop sharing"})).toBeNull();
  });

  it("does not grant creator Stop outside volume scope", async () => {
    api.getEntityShare.mockResolvedValue({scope: "project", active: true, aggregate_state: "all", shares: [{id: 9, scope: "project", url: "/share/public/project", created_by: 4}]});
    render(<ShareControl scope="project" projectId={1}/>);

    expect(await screen.findByRole("button", {name: "Copy link"})).toBeTruthy();
    expect(screen.queryByRole("button", {name: "Stop sharing"})).toBeNull();
  });

  it("refreshes position when Copy link is used on an existing share", async () => {
    api.getEntityShare.mockResolvedValue({scope: "volume", active: true, aggregate_state: "shared", shares: [{id: 9, scope: "volume", url: "/share/public/old", created_by: 4}]});
    let z = 2;
    render(<ShareControl scope="volume" projectId={1} volumeId={3} getViewLocation={() => ({z, y: 3, x: 4, axis: "z"})}/>);
    z = 8;
    fireEvent.click(await screen.findByRole("button", {name: "Copy link"}));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      "http://localhost:3000/share/public/old?z=8&y=3&x=4&axis=z",
    ));
    expect(screen.queryByText(/Link copied with current position/i)).toBeNull();
  });

  it("shows a width-stable Copied confirmation, then reverts", async () => {
    api.getEntityShare.mockResolvedValue({scope: "volume", active: true, aggregate_state: "shared", shares: [{id: 9, scope: "volume", url: "/share/public/old", created_by: 2}]});
    render(<ShareControl scope="volume" projectId={1} volumeId={3}/>);
    const copy = await screen.findByRole("button", {name: "Copy link"});
    expect(copy.classList.contains("share-copy-button")).toBe(true);

    vi.useFakeTimers();
    fireEvent.click(copy);
    await act(async () => Promise.resolve());
    expect(screen.getByRole("button", {name: "Copied"})).toBe(copy);
    act(() => vi.advanceTimersByTime(2000));
    expect(screen.getByRole("button", {name: "Copy link"})).toBe(copy);
    vi.useRealTimers();
  });

  it("does not claim Copied when the clipboard write fails", async () => {
    api.getEntityShare.mockResolvedValue({scope: "volume", active: true, aggregate_state: "shared", shares: [{id: 9, scope: "volume", url: "/share/public/old", created_by: 4}]});
    vi.mocked(navigator.clipboard.writeText).mockRejectedValueOnce(new Error("clipboard denied"));
    render(<ShareControl scope="volume" projectId={1} volumeId={3}/>);

    fireEvent.click(await screen.findByRole("button", {name: "Copy link"}));
    expect(await screen.findByText("Could not copy link. Try again.")).toBeTruthy();
    expect(screen.queryByRole("button", {name: "Copied"})).toBeNull();
    expect(screen.getByRole("button", {name: "Copy link"})).toBeTruthy();
  });

  it("lets only a manager stop without reloading its containing page", async () => {
    auth.isManager = true;
    api.getEntityShare.mockResolvedValue({scope: "volume", active: true, aggregate_state: "shared", shares: [{id: 9, scope: "volume", url: "/share/public/old", created_by: 4}]});
    render(<ShareControl scope="volume" projectId={1} volumeId={3}/>);
    fireEvent.click(await screen.findByRole("button", {name: "Stop sharing"}));
    await waitFor(() => expect(api.revokePublicShare).toHaveBeenCalledWith(9));
    expect(screen.getByLabelText("Sharing off")).toBeTruthy();
  });
});
