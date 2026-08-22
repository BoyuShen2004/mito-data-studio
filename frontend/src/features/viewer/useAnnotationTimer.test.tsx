import { render, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  startTaskTiming: vi.fn(),
  heartbeatTaskTiming: vi.fn(),
  stopTaskTiming: vi.fn(),
}));
vi.mock("../../api/timing", () => api);

import { useAnnotationTimer } from "./useAnnotationTimer";

const CONFIG = {
  heartbeat_seconds: 30,
  hidden_grace_seconds: 60,
  idle_seconds: 120,
  abandon_grace_seconds: 0,
  max_interval_seconds: 120,
  server_idle_timeout_seconds: 300,
};

const status = (over: Record<string, unknown> = {}) => ({
  task_id: 5,
  volume_id: 9,
  tracking: true,
  eligible: true,
  reason: "ok",
  session_id: "sess-1",
  total_seconds: 0,
  display: "0m",
  config: CONFIG,
  ...over,
});

function Harness({ taskId = 5, enabled = true }: { taskId?: number; enabled?: boolean }) {
  const state = useAnnotationTimer(taskId, enabled);
  return <span data-testid="state">{JSON.stringify(state)}</span>;
}

let beacon: ReturnType<typeof vi.fn>;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers();
  api.startTaskTiming.mockReset().mockResolvedValue(status());
  api.heartbeatTaskTiming.mockReset().mockResolvedValue(status({ total_seconds: 30 }));
  api.stopTaskTiming.mockReset().mockResolvedValue(status({ tracking: false }));
  beacon = vi.fn(() => true);
  fetchMock = vi.fn(() => Promise.resolve(new Response("{}")));
  Object.defineProperty(navigator, "sendBeacon", { value: beacon, configurable: true });
  vi.stubGlobal("fetch", fetchMock);
  window.sessionStorage.clear();
  Object.defineProperty(document, "hidden", { value: false, configurable: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** Let the start promise settle, then advance the heartbeat interval. */
const settle = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

const advance = async (ms: number) => {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe("useAnnotationTimer", () => {
  it("does not start when disabled — a read-only viewer never counts", async () => {
    render(<Harness enabled={false} />);
    await settle();
    await advance(120_000);
    expect(api.startTaskTiming).not.toHaveBeenCalled();
    expect(api.heartbeatTaskTiming).not.toHaveBeenCalled();
  });

  it("starts once for the editable editor and heartbeats on the server's cadence", async () => {
    render(<Harness />);
    await settle();
    expect(api.startTaskTiming).toHaveBeenCalledTimes(1);

    await advance(30_000);
    expect(api.heartbeatTaskTiming).toHaveBeenCalledTimes(1);
    await advance(30_000);
    expect(api.heartbeatTaskTiming).toHaveBeenCalledTimes(2);
    // The cadence came from the server, not from a hardcoded client constant.
    expect(api.heartbeatTaskTiming).toHaveBeenCalledWith(5, "sess-1");
  });

  it("reuses one per-tab token so a remount resumes rather than duplicating", async () => {
    const first = render(<Harness />);
    await settle();
    const token = api.startTaskTiming.mock.calls[0][1];
    first.unmount();
    await settle();

    render(<Harness />);
    await settle();
    expect(api.startTaskTiming.mock.calls[1][1]).toBe(token);
  });

  it("stops on unmount — leaving the editor ends the session", async () => {
    const view = render(<Harness />);
    await settle();
    view.unmount();
    await settle();
    expect(fetchMock).toHaveBeenCalled();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/tasks/5/timing/stop/");
    expect(JSON.parse((init as RequestInit).body as string)).toMatchObject({
      session_id: "sess-1",
      reason: "left_editor",
    });
  });

  it("uses sendBeacon on pagehide, because a dying page cannot finish a fetch", async () => {
    render(<Harness />);
    await settle();
    await act(async () => {
      window.dispatchEvent(new Event("pagehide"));
    });
    expect(beacon).toHaveBeenCalledTimes(1);
    expect(beacon.mock.calls[0][0]).toBe("/api/tasks/5/timing/stop/");
  });

  it("stops heartbeating once the tab has been hidden past the grace period", async () => {
    render(<Harness />);
    await settle();
    await advance(30_000);
    expect(api.heartbeatTaskTiming).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    // Within the 60 s grace, it keeps counting...
    await advance(30_000);
    expect(api.heartbeatTaskTiming).toHaveBeenCalledTimes(2);
    // ...past it, it stops, and stays stopped however long the tab sits there.
    await advance(60_000);
    const afterStop = api.heartbeatTaskTiming.mock.calls.length;
    await advance(8 * 60 * 60 * 1000);
    expect(api.heartbeatTaskTiming.mock.calls.length).toBe(afterStop);
  });

  it("resumes when the tab becomes visible again", async () => {
    render(<Harness />);
    await settle();
    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    await advance(120_000);
    const starts = api.startTaskTiming.mock.calls.length;

    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    await settle();
    expect(api.startTaskTiming.mock.calls.length).toBe(starts + 1);
  });

  it("pauses on idle and resumes on ordinary annotation activity", async () => {
    render(<Harness />);
    await settle();
    // No pointer/key activity for longer than the idle window.
    await advance(180_000);
    const paused = api.heartbeatTaskTiming.mock.calls.length;
    await advance(60_000);
    expect(api.heartbeatTaskTiming.mock.calls.length).toBe(paused);

    // Any ordinary interaction brings it back.
    await act(async () => {
      window.dispatchEvent(new Event("pointerdown"));
    });
    const startsBefore = api.startTaskTiming.mock.calls.length;
    await advance(30_000);
    expect(api.startTaskTiming.mock.calls.length).toBe(startsBefore + 1);
  });

  it("survives a timing outage without throwing at the editor", async () => {
    api.startTaskTiming.mockRejectedValue(new Error("timing down"));
    api.heartbeatTaskTiming.mockRejectedValue(new Error("timing down"));
    const view = render(<Harness />);
    await settle();
    await advance(60_000);
    // No unhandled rejection, no crash, and the editor is still mounted.
    expect(view.getByTestId("state")).toBeTruthy();
    expect(JSON.parse(view.getByTestId("state").textContent!).tracking).toBe(false);
  });

  it("reports a legacy-exempt task as not tracked and never heartbeats it", async () => {
    api.startTaskTiming.mockResolvedValue(
      status({ tracking: false, eligible: false, reason: "legacy_exempt", session_id: null, total_seconds: null, display: "-" }),
    );
    const view = render(<Harness />);
    await settle();
    await advance(120_000);
    expect(api.heartbeatTaskTiming).not.toHaveBeenCalled();
    const state = JSON.parse(view.getByTestId("state").textContent!);
    expect(state.tracking).toBe(false);
    expect(state.eligible).toBe(false);
    expect(state.totalSeconds).toBe(null);
  });

  it("keeps the server's total authoritative between heartbeats", async () => {
    api.heartbeatTaskTiming.mockResolvedValue(status({ total_seconds: 300 }));
    const view = render(<Harness />);
    await settle();
    await advance(30_000);
    // `advance` already flushes the microtask queue, so the assertion is
    // direct. `waitFor` cannot be used here: it polls on real timers, which
    // fake timers have replaced, so it would simply hang.
    const state = JSON.parse(view.getByTestId("state").textContent!);
    expect(state.totalSeconds).toBe(300);
  });
});
