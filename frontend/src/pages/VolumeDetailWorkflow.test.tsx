import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route, Link } from "react-router-dom";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import VolumeDetailPage from "./VolumeDetailPage";
import { listProjectTasks } from "../api/tasks";
import { getVolume, buildVolumePyramid } from "../api/volumes";
import type { Volume } from "../types/volume";

vi.mock("../api/volumes", () => ({ getVolume: vi.fn(), buildVolumePyramid: vi.fn(), editVolume: vi.fn() }));
vi.mock("../api/tasks", () => ({ listProjectTasks: vi.fn().mockResolvedValue([]) }));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ isManager: true, isRequester: false }) }));
vi.mock("../components/ShareControl", () => ({ default: () => null }));
vi.mock("../components/DeleteButton", () => ({ default: () => null }));
const volume = { id: 3, project: 1, name: "Volume A", image_path: "/raw/a.tif", region_mask_path: "", label_path: "", label_type: "none", streaming_status: "building", region_streaming_status: "absent", metadata: {} } as Volume;
const open = () => render(<MemoryRouter initialEntries={["/volumes/3"]}>
  <Link to="/volumes/4">Other volume</Link>
  <Routes><Route path="/volumes/:id" element={<VolumeDetailPage />} /></Routes>
</MemoryRouter>);
beforeEach(() => { vi.clearAllMocks(); vi.mocked(getVolume).mockReset().mockResolvedValue(volume); });
afterEach(() => { vi.restoreAllMocks(); });

it("preserves pending metadata while pyramid status polls and finishes", async () => {
  const interval = vi.spyOn(window, "setInterval").mockReturnValue(123);
  vi.spyOn(window, "clearInterval").mockImplementation(() => {});
  open();
  const name = await screen.findByRole("textbox", { name: "Name" });
  fireEvent.change(name, { target: { value: "Pending volume name" } });
  await waitFor(() => expect(interval).toHaveBeenCalledWith(expect.any(Function), 3000));
  let finish!: (value: Volume) => void;
  vi.mocked(getVolume).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  act(() => (interval.mock.calls.find(call => call[1] === 3000)![0] as () => void)());
  await waitFor(() => expect(getVolume).toHaveBeenCalledTimes(2));
  expect(name.isConnected).toBe(true);
  expect(interval.mock.calls.filter(call => call[1] === 3000)).toHaveLength(1);
  expect(window.clearInterval).toHaveBeenCalledWith(123);
  await act(async () => finish({ ...volume, streaming_status: "ready" }));
  expect(screen.getByRole("textbox", { name: "Name" })).toBe(name);
  expect((name as HTMLInputElement).value).toBe("Pending volume name");
  expect(screen.getByRole("button", { name: "Save metadata" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Rebuild pyramid" })).toBeTruthy();
  // Polling metadata must not fetch every task in the project again.
  expect(listProjectTasks).toHaveBeenCalledTimes(1);
  expect(listProjectTasks).toHaveBeenCalledWith(1);
});

it("retains metadata on a refresh failure and allows retry", async () => {
  vi.mocked(getVolume).mockResolvedValueOnce({ ...volume, streaming_status: "not_built" })
    .mockRejectedValueOnce(new Error("Refresh unavailable"))
    .mockResolvedValueOnce({ ...volume, streaming_status: "ready" });
  vi.mocked(buildVolumePyramid).mockResolvedValue({ job_id: 1, volume });
  open();
  const name = await screen.findByRole("textbox", { name: "Name" });
  fireEvent.change(name, { target: { value: "Keep this draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Build pyramid" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Refresh unavailable");
  expect(name.isConnected).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("button", { name: "Rebuild pyramid" });
  expect(screen.queryByRole("alert")).toBeNull();
  expect((name as HTMLInputElement).value).toBe("Keep this draft");
});

it("starts separate metadata state when navigating to another volume", async () => {
  vi.mocked(getVolume).mockResolvedValueOnce({ ...volume, streaming_status: "not_built" })
    .mockResolvedValueOnce({ ...volume, id: 4, name: "Volume B", image_path: "/raw/b.tif", streaming_status: "not_built" });
  open();
  fireEvent.change(await screen.findByRole("textbox", { name: "Name" }), { target: { value: "Only for A" } });
  fireEvent.click(screen.getByRole("link", { name: "Other volume" }));
  await screen.findByRole("heading", { name: "Volume B" });
  expect((screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe("Volume B");
  expect(screen.queryByRole("button", { name: "Save metadata" })).toBeNull();
});
