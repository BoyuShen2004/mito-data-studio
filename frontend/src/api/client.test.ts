import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, setToken } from "./client";

describe("API transport", () => {
  const storage = new Map<string, string>();

  beforeEach(() => {
    vi.restoreAllMocks();
    storage.clear();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
      clear: () => storage.clear(),
    });
  });

  it("normalizes API paths and authenticates binary POST requests", async () => {
    setToken("test-token");
    const payload = new Uint8Array([1, 2, 3]).buffer;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(payload, {
        status: 200,
        headers: { "Content-Type": "application/octet-stream" },
      }),
    );

    const result = await api.postArrayBuffer("/tasks/7/3d/", { axis: "z" });

    expect([...new Uint8Array(result)]).toEqual([1, 2, 3]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/tasks/7/3d/",
      expect.objectContaining({
        method: "POST",
        headers: {
          Authorization: "Token test-token",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ axis: "z" }),
      }),
    );
  });

  it("does not double-prefix an existing /api path", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await api.get("/api/identity/");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/identity/");
  });

  it("preserves useful non-JSON proxy errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("upstream unavailable", {
        status: 502,
        statusText: "Bad Gateway",
      }),
    );

    await expect(api.get("/projects/")).rejects.toMatchObject({
      status: 502,
      message: "upstream unavailable",
    } satisfies Partial<ApiError>);
  });

  it("never exposes an HTML proxy error document", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("<!doctype html><html><body>cloud proxy diagnostic dump</body></html>", {
        status: 502,
        headers: {"Content-Type": "text/html"},
      }),
    );

    await expect(api.post("/projects/1/assign-plan/rows/", {})).rejects.toMatchObject({
      status: 502,
      message: "Server error (502)",
    } satisfies Partial<ApiError>);
  });
});
