import { describe, expect, it, vi } from "vitest";
import { ByteLru } from "./byteLru";

describe("ByteLru", () => {
  it("evicts by bytes in deterministic recency order", () => {
    const eviction = vi.fn();
    const cache = new ByteLru<string, { byteLength: number }>(10, {
      hit() {},
      miss() {},
      eviction,
    });
    cache.set("a", { byteLength: 4 });
    cache.set("b", { byteLength: 4 });
    expect(cache.get("a")).toBeDefined();
    cache.set("c", { byteLength: 4 });
    expect(cache.peek("b")).toBeUndefined();
    expect(cache.keys()).toEqual(["a", "c"]);
    expect(cache.bytes).toBe(8);
    expect(eviction).toHaveBeenCalledWith(4);
  });

  it("does not retain a single value larger than the budget", () => {
    const cache = new ByteLru<string, { byteLength: number }>(3);
    cache.set("large", { byteLength: 4 });
    expect(cache.size).toBe(0);
    expect(cache.bytes).toBe(0);
  });

  it("clears references and supports scoped deletion", () => {
    const cache = new ByteLru<string, { byteLength: number }>(20);
    cache.set("v1:a", { byteLength: 5 });
    cache.set("v2:a", { byteLength: 5 });
    expect(cache.deleteWhere((key) => key.startsWith("v1:"))).toBe(1);
    expect(cache.keys()).toEqual(["v2:a"]);
    cache.clear();
    expect(cache.bytes).toBe(0);
    expect(cache.keys()).toEqual([]);
  });
});
