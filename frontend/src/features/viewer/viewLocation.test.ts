import { describe, expect, it } from "vitest";
import { parseViewLocation, replaceViewLocation, withViewLocation } from "./viewLocation";

describe("viewer share location", () => {
  it("round-trips xyz, axis, and active label on a public share URL", () => {
    const url = withViewLocation("/share/public/revocable-token", {z: 9, y: 17, x: 23, axis: "x", label: 6});
    const parsed = parseViewLocation(new URL(url).search);
    expect(parsed).toEqual({z: 9, y: 17, x: 23, axis: "x", label: 6});
    expect(url).toContain("/share/public/revocable-token?");
  });

  it("drops stale position params and omits a missing active label", () => {
    const url = withViewLocation("/share/public/token?z=99&label=42", {z: 1, y: 2, x: 3, axis: "z"});
    expect(new URL(url).searchParams.get("label")).toBeNull();
    expect(parseViewLocation(new URL(url).search)).toEqual({z: 1, y: 2, x: 3, axis: "z"});
  });

  it("replaces the current URL so refresh restores the latest plane", () => {
    window.history.replaceState({}, "", "/viewer/tasks/30?keep=yes&submission=9");
    replaceViewLocation({z: 29, y: 14, x: 9, axis: "z", label: 6});
    expect(window.location.pathname + window.location.search).toBe(
      "/viewer/tasks/30?keep=yes&submission=9&z=29&y=14&x=9&axis=z&label=6",
    );
  });

  it("does not treat label-only search as a saved plane", async () => {
    const { hasViewCoordinates } = await import("./viewLocation");
    expect(hasViewCoordinates("?label=1292&feedback=11&submission=9")).toBe(false);
    expect(hasViewCoordinates("?z=40&label=1292")).toBe(true);
  });
});
