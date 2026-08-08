import { describe, expect, it } from "vitest";
import { parseViewLocation, withViewLocation } from "./viewLocation";

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
});
