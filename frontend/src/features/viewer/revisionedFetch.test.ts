import { describe, expect, it } from "vitest";
import { RevisionedFetch } from "./revisionedFetch";

describe("RevisionedFetch", () => {
  it("returns a read when no invalidation occurs", async () => {
    const gate = new RevisionedFetch();
    await expect(gate.loadLatest(async () => 7)).resolves.toBe(7);
  });

  it("retries a stale read that finishes after invalidation", async () => {
    const gate = new RevisionedFetch();
    let resolveFirst!: (value: string) => void;
    let calls = 0;

    const result = gate.loadLatest(async () => {
      calls += 1;
      if (calls === 1) {
        return new Promise<string>((resolve) => {
          resolveFirst = resolve;
        });
      }
      return "after-save";
    });

    // Save completes while the pre-save read is still in flight.
    gate.invalidate();
    resolveFirst("before-save");

    await expect(result).resolves.toBe("after-save");
    expect(calls).toBe(2);
  });

  it("retries once per invalidation observed during a read", async () => {
    const gate = new RevisionedFetch();
    let calls = 0;
    const result = gate.loadLatest(async () => {
      calls += 1;
      if (calls < 3) gate.invalidate();
      return calls;
    });

    await expect(result).resolves.toBe(3);
  });
});
