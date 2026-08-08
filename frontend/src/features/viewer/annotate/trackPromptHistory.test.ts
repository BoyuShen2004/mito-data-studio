import { describe, expect, it } from "vitest";

import type { TrackingPrompt } from "../../../api/viewer";
import CANVAS_SOURCE_IMPORT from "../AnnotationCanvas.tsx?raw";
import STYLES_SOURCE_IMPORT from "../../../styles.css?raw";
import {
  restoreTrackingPromptGeometry,
  snapshotTrackingPromptGeometry,
} from "./trackingPromptHistory";

const SOURCE: string = CANVAS_SOURCE_IMPORT;
const STYLES: string = STYLES_SOURCE_IMPORT;

const prompt = (parentId: number, childIndexes: number[], runLength = 1): TrackingPrompt => ({
  parent_id: parentId,
  subclasses: childIndexes.map((index) => ({
    index,
    seeds: [{ z: 3, shape: [2, 2], rle: [[0, runLength]] }],
  })),
  z_range: [3, 3],
  status: "ready",
});

describe("Track prompt history wiring", () => {
  it("records only seed-mask commits, not queue or class mutations", () => {
    expect(SOURCE).toContain("const recordTrackingHistory");
    expect(SOURCE).toMatch(/recordTrackingHistory\(\);\s+setTrackingPrompts/);
    expect((SOURCE.match(/recordTrackingHistory\(\);/g) ?? [])).toHaveLength(1);
  });

  it("restores prompt geometry into the current durable queue", () => {
    expect(SOURCE).toContain("restoreTrackingPromptGeometry(trackingPrompts, target)");
    expect(SOURCE).toContain("await replaceTrackingPrompts(taskId, restoredItems)");
    expect(SOURCE).toContain("if (trackPromptTool != null) undoTrackingPrompt();");
    expect(SOURCE).toContain("if (trackPromptTool != null) redoTrackingPrompt();");
  });

  it("undoes a brush mask without removing parents or children added later", () => {
    const beforeBrush = [prompt(10, [1], 1)];
    const afterBrushAndQueueChanges = [prompt(10, [1, 2], 2), prompt(20, [1], 3)];

    const restored = restoreTrackingPromptGeometry(
      afterBrushAndQueueChanges,
      snapshotTrackingPromptGeometry(beforeBrush),
    );

    expect(restored.map((item) => item.parent_id)).toEqual([10, 20]);
    expect(restored[0].subclasses.map((child) => child.index)).toEqual([1, 2]);
    expect(restored[0].subclasses[0].seeds[0].rle).toEqual([[0, 1]]);
    expect(restored[0].subclasses[1].seeds[0].rle).toEqual([[0, 2]]);
    expect(restored[1].subclasses[0].seeds[0].rle).toEqual([[0, 3]]);
  });

  it("does not resurrect a removed parent or child while restoring masks", () => {
    const snapshot = snapshotTrackingPromptGeometry([prompt(10, [1, 2]), prompt(20, [1])]);
    const current = [prompt(10, [2], 4)];

    const restored = restoreTrackingPromptGeometry(current, snapshot);

    expect(restored).toHaveLength(1);
    expect(restored[0].parent_id).toBe(10);
    expect(restored[0].subclasses.map((child) => child.index)).toEqual([2]);
  });

  it("lets the prompt-tools box hug content when the size slider is irrelevant", () => {
    expect(STYLES).toMatch(/\.track-prompt-tools\s*\{[^}]*flex:\s*0 0 auto;/s);
    expect(STYLES).toMatch(/\.track-size-control\.inactive\s*\{[^}]*display:\s*none;/s);
  });
});
