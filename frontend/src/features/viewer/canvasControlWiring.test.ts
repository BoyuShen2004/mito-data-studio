import { describe, expect, it } from "vitest";
import ANNOTATION_SOURCE_IMPORT from "./AnnotationCanvas.tsx?raw";
import SLICE_VIEWER_SOURCE_IMPORT from "./SliceViewer.tsx?raw";

const annotationSource = String(ANNOTATION_SOURCE_IMPORT);
const sliceViewerSource = String(SLICE_VIEWER_SOURCE_IMPORT);

function buttonWithTitle(source: string, title: string) {
  const titleAt = source.indexOf(`title="${title}"`);
  expect(titleAt).toBeGreaterThan(-1);
  const start = source.lastIndexOf("<button", titleAt);
  const end = source.indexOf("</button>", titleAt);
  return source.slice(start, end + "</button>".length);
}

describe("layer button and keyboard pan wiring", () => {
  it.each([
    ["Previous layer", "jump(-1)"],
    ["Next layer", "jump(1)"],
    ["Previous layer (large step)", "jump(-10)"],
    ["Next layer (large step)", "jump(10)"],
  ])("makes AnnotationCanvas %s change layer, not pan", (title, handler) => {
    const button = buttonWithTitle(annotationSource, title);
    expect(button).toContain(handler);
    expect(button).not.toMatch(/panCanvas|panViewport/);
  });

  it.each([
    ["Previous layer", "step(-1)"],
    ["Next layer", "step(1)"],
    ["Previous layer (large step)", "step(-10)"],
    ["Next layer (large step)", "step(10)"],
  ])("makes SliceViewer %s change layer, not pan", (title, handler) => {
    const button = buttonWithTitle(sliceViewerSource, title);
    expect(button).toContain(handler);
    expect(button).not.toMatch(/panCanvas/);
  });

  it("maps AnnotationCanvas A/D to layers and all arrow keys to pan", () => {
    expect(annotationSource).toMatch(/e\.key === "a"[\s\S]{0,80}jump\(-1\)/);
    expect(annotationSource).toMatch(/e\.key === "d"[\s\S]{0,80}jump\(1\)/);
    expect(annotationSource).toMatch(/e\.key === "ArrowLeft"[\s\S]{0,100}panViewport\("left"\)/);
    expect(annotationSource).toMatch(/e\.key === "ArrowRight"[\s\S]{0,100}panViewport\("right"\)/);
    expect(annotationSource).toMatch(/e\.key === "ArrowUp"[\s\S]{0,100}panViewport\("up"\)/);
    expect(annotationSource).toMatch(/e\.key === "ArrowDown"[\s\S]{0,100}panViewport\("down"\)/);
  });

  it("maps SliceViewer A/D to layers and all arrow keys to pan", () => {
    expect(sliceViewerSource).toMatch(/e\.key === "a"[\s\S]{0,80}step\(-1\)/);
    expect(sliceViewerSource).toMatch(/e\.key === "d"[\s\S]{0,80}step\(1\)/);
    expect(sliceViewerSource).toMatch(/e\.key === "ArrowLeft"[\s\S]{0,130}panCanvasHorizontally\(viewportRef\.current, -1\)/);
    expect(sliceViewerSource).toMatch(/e\.key === "ArrowRight"[\s\S]{0,130}panCanvasHorizontally\(viewportRef\.current, 1\)/);
    expect(sliceViewerSource).toMatch(/e\.key === "ArrowUp"[\s\S]{0,130}panCanvasVertically\(viewportRef\.current, -1\)/);
    expect(sliceViewerSource).toMatch(/e\.key === "ArrowDown"[\s\S]{0,130}panCanvasVertically\(viewportRef\.current, 1\)/);
  });
});

describe("Track review wiring", () => {
  it("keeps Confirm/Reject off the propagation busy state and refreshes labels", () => {
    const start = annotationSource.indexOf("const reviewTrackPreview = useCallback");
    const end = annotationSource.indexOf("useEffect(() => {", start);
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    const review = annotationSource.slice(start, end);
    expect(review).toContain("setTrackReviewAction(action)");
    expect(review).toContain("reviewTrackingPreview(taskId, action)");
    expect(review).toContain("loadSlice(index, undefined, { forceServer: true })");
    expect(review).toContain("setLabelsSummaryToken");
    expect(review).not.toContain("setTracking(");
  });
});
