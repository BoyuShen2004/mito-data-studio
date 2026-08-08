import { describe, expect, it } from "vitest";
import { panCanvasHorizontally, panCanvasVertically } from "./canvasPan";

describe("panCanvasHorizontally", () => {
  it("moves only horizontal scroll position in the requested direction", () => {
    const viewport = document.createElement("div");
    Object.defineProperty(viewport, "clientWidth", { value: 500 });
    viewport.scrollLeft = 400;
    viewport.scrollTop = 75;

    panCanvasHorizontally(viewport, -1);
    expect(viewport.scrollLeft).toBe(300);
    expect(viewport.scrollTop).toBe(75);

    panCanvasHorizontally(viewport, 1);
    expect(viewport.scrollLeft).toBe(400);
    expect(viewport.scrollTop).toBe(75);
  });

  it("uses a larger step for the double-arrow controls", () => {
    const viewport = document.createElement("div");
    Object.defineProperty(viewport, "clientWidth", { value: 800 });
    viewport.scrollLeft = 1_000;

    panCanvasHorizontally(viewport, -1, true);
    expect(viewport.scrollLeft).toBe(400);
  });

  it("pans vertically without changing horizontal position", () => {
    const viewport = document.createElement("div");
    Object.defineProperty(viewport, "clientHeight", { value: 500 });
    viewport.scrollLeft = 75;
    viewport.scrollTop = 400;

    panCanvasVertically(viewport, -1);
    expect(viewport.scrollTop).toBe(300);
    expect(viewport.scrollLeft).toBe(75);

    panCanvasVertically(viewport, 1);
    expect(viewport.scrollTop).toBe(400);
    expect(viewport.scrollLeft).toBe(75);
  });
});
