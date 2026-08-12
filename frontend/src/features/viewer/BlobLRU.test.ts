import { afterEach, describe, expect, it, vi } from "vitest";
import { BlobLRU } from "./SliceViewer";

describe("SliceViewer BlobLRU", () => {
  afterEach(() => vi.restoreAllMocks());

  it("revokes replacements, evictions, and remaining URLs on clear", () => {
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const cache = new BlobLRU(2);
    cache.set("a", "blob:a1");
    cache.set("a", "blob:a2");
    cache.set("b", "blob:b");
    cache.set("c", "blob:c");

    expect(revoke).toHaveBeenCalledWith("blob:a1");
    expect(revoke).toHaveBeenCalledWith("blob:a2");
    cache.clear();
    expect(revoke).toHaveBeenCalledWith("blob:b");
    expect(revoke).toHaveBeenCalledWith("blob:c");
  });
});
