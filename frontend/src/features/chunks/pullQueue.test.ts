import { describe, expect, it, vi } from "vitest";
import {
  PullCancelledError,
  PullPriority,
  PullQueue,
  PullQueueDisposedError,
  StaleGenerationError,
} from "./pullQueue";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function request(
  key: string,
  run: (signal: AbortSignal) => Promise<string>,
  priority = PullPriority.CURRENT,
  volumeScope = "volume-a",
  generation = 1,
) {
  return {
    key,
    volumeScope,
    viewport: "main",
    generation,
    priority,
    run,
  };
}

describe("PullQueue", () => {
  it("bounds global and per-volume concurrency", async () => {
    const queue = new PullQueue({ maxActive: 2, maxActivePerVolume: 1 });
    const gates = [deferred<string>(), deferred<string>(), deferred<string>()];
    let active = 0;
    let peak = 0;
    const run = (gate: ReturnType<typeof deferred<string>>) => async () => {
      active += 1;
      peak = Math.max(peak, active);
      const value = await gate.promise;
      active -= 1;
      return value;
    };
    const a = queue.enqueue(request("a", run(gates[0]), PullPriority.CURRENT, "v1"));
    const b = queue.enqueue(request("b", run(gates[1]), PullPriority.CURRENT, "v1"));
    const c = queue.enqueue(request("c", run(gates[2]), PullPriority.CURRENT, "v2"));
    expect(queue.inspectInFlight().map((item) => item.key).sort()).toEqual(["a", "c"]);
    expect(queue.inspectQueue().map((item) => item.key)).toEqual(["b"]);
    gates[0].resolve("a");
    await a.promise;
    await vi.waitFor(() => {
      expect(queue.inspectInFlight().some((item) => item.key === "b")).toBe(true);
    });
    gates[1].resolve("b");
    gates[2].resolve("c");
    await Promise.all([b.promise, c.promise]);
    expect(peak).toBe(2);
    queue.dispose();
  });

  it("keeps current work ahead of speculative prefetch", async () => {
    const queue = new PullQueue({ maxActive: 1 });
    const blocker = deferred<string>();
    const order: string[] = [];
    const first = queue.enqueue(request("first", () => blocker.promise));
    const prefetch = queue.enqueue(
      request("prefetch", async () => {
        order.push("prefetch");
        return "p";
      }, PullPriority.PREFETCH),
    );
    const current = queue.enqueue(
      request("current", async () => {
        order.push("current");
        return "c";
      }),
    );
    blocker.resolve("first");
    await first.promise;
    await current.promise;
    await prefetch.promise;
    expect(order).toEqual(["current", "prefetch"]);
    queue.dispose();
  });

  it("collapses duplicates while preserving independent consumers", async () => {
    const queue = new PullQueue();
    const gate = deferred<string>();
    const run = vi.fn(() => gate.promise);
    const first = queue.enqueue(request("same", run));
    const second = queue.enqueue(request("same", run));
    first.cancel();
    await expect(first.promise).rejects.toBeInstanceOf(PullCancelledError);
    expect(run).toHaveBeenCalledTimes(1);
    gate.resolve("shared");
    await expect(second.promise).resolves.toBe("shared");
    queue.dispose();
  });

  it("aborts underlying work when its final consumer cancels", async () => {
    const queue = new PullQueue();
    let aborted = false;
    const handle = queue.enqueue(
      request(
        "abort",
        (signal) =>
          new Promise((_resolve, reject) => {
            signal.addEventListener("abort", () => {
              aborted = true;
              reject(new DOMException("aborted", "AbortError"));
            });
          }),
      ),
    );
    handle.cancel();
    await expect(handle.promise).rejects.toBeInstanceOf(PullCancelledError);
    expect(aborted).toBe(true);
    queue.dispose();
  });

  it("allows a fresh same-key request immediately after cancellation", async () => {
    const queue = new PullQueue({ maxActive: 1 });
    const first = queue.enqueue(
      request(
        "same-key",
        (signal) =>
          new Promise((_resolve, reject) => {
            signal.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            );
          }),
      ),
    );
    first.cancel();
    const replacement = queue.enqueue(
      request("same-key", async () => "fresh response"),
    );
    await expect(first.promise).rejects.toBeInstanceOf(PullCancelledError);
    await expect(replacement.promise).resolves.toBe("fresh response");
    queue.dispose();
  });

  it("rejects an out-of-order completion from a stale generation", async () => {
    const queue = new PullQueue();
    const gate = deferred<string>();
    queue.setGeneration("main", 1);
    const old = queue.enqueue(request("old", () => gate.promise, PullPriority.CURRENT, "v", 1));
    queue.setGeneration("main", 2, false);
    gate.resolve("old pixels");
    await expect(old.promise).rejects.toBeInstanceOf(StaleGenerationError);
    queue.dispose();
  });

  it("does not allow an old viewport to move the generation backwards", () => {
    const queue = new PullQueue();
    queue.setGeneration("main", 4);
    expect(() => queue.setGeneration("main", 3)).toThrow(RangeError);
    queue.dispose();
  });

  it("retries only when the request classifies the error as transient", async () => {
    const queue = new PullQueue({ maxRetries: 2, backoffMs: () => 0 });
    let attempts = 0;
    const handle = queue.enqueue({
      ...request("retry", async () => {
        attempts += 1;
        if (attempts < 3) throw new Error("temporary");
        return "ok";
      }),
      shouldRetry: () => true,
    });
    await expect(handle.promise).resolves.toBe("ok");
    expect(attempts).toBe(3);
    queue.dispose();
  });

  it("reprioritizes queued work deterministically", async () => {
    const queue = new PullQueue({ maxActive: 1 });
    const blocker = deferred<string>();
    const first = queue.enqueue(request("first", () => blocker.promise));
    const order: string[] = [];
    const a = queue.enqueue(
      request("a", async () => {
        order.push("a");
        return "a";
      }, PullPriority.PREFETCH),
    );
    const b = queue.enqueue(
      request("b", async () => {
        order.push("b");
        return "b";
      }, PullPriority.NEAR),
    );
    a.reprioritize(PullPriority.CURRENT);
    blocker.resolve("done");
    await Promise.all([first.promise, a.promise, b.promise]);
    expect(order).toEqual(["a", "b"]);
    queue.dispose();
  });

  it("disposal rejects queued consumers and clears state", async () => {
    const queue = new PullQueue({ maxActive: 1 });
    const blocker = deferred<string>();
    const active = queue.enqueue(request("active", () => blocker.promise));
    const queued = queue.enqueue(request("queued", async () => "never"));
    queue.dispose();
    await expect(active.promise).rejects.toBeInstanceOf(PullQueueDisposedError);
    await expect(queued.promise).rejects.toBeInstanceOf(PullQueueDisposedError);
    expect(queue.inspectQueue()).toEqual([]);
    expect(queue.inspectInFlight()).toEqual([]);
  });
});
