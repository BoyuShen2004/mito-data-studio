import { describe, expect, it } from "vitest";

import CANVAS_SOURCE_IMPORT from "../AnnotationCanvas.tsx?raw";

const CANVAS_SOURCE: string = CANVAS_SOURCE_IMPORT;
const TRACK_HANDLERS = CANVAS_SOURCE.slice(
  CANVAS_SOURCE.indexOf("const onTrackPromptPointerDown"),
  CANVAS_SOURCE.indexOf("const jump = useCallback"),
);

describe("Track Box/Point proposal lifecycle wiring", () => {
  it("stages predictions without durably saving them in the prediction callbacks", () => {
    for (const predictor of ["predictMaskFromPoints", "predictMaskFromBox"]) {
      const start = TRACK_HANDLERS.indexOf(predictor);
      const end = TRACK_HANDLERS.indexOf("} catch (error)", start);
      const predictionCallback = TRACK_HANDLERS.slice(start, end);
      expect(predictionCallback).toContain("stageTrackingProposal");
      expect(predictionCallback).not.toContain("saveTrackingPromptMask");
    }
  });

  it("banks the pending proposal before starting the next Box or Point object", () => {
    // Without this, a second gesture replaced a proposal that had never been
    // committed, so Box and Point could only ever leave one object on a layer
    // while Brush accumulated freely.
    for (const predictor of ["predictMaskFromPoints", "predictMaskFromBox"]) {
      const start = TRACK_HANDLERS.lastIndexOf("const prediction = (async () => {",
        TRACK_HANDLERS.indexOf(predictor));
      const gesture = TRACK_HANDLERS.slice(start, TRACK_HANDLERS.indexOf(predictor));
      expect(gesture).toContain("commitTrackingProposalRef.current()");
      // The sequence number must be taken after the commit, which bumps it —
      // otherwise the new prediction looks stale the moment it arrives.
      expect(gesture.indexOf("commitTrackingProposalRef.current()"))
        .toBeLessThan(gesture.indexOf("++trackPromptPredictSeqRef.current"));
    }
  });

  it("commits Track proposals only through finalize gestures and discards them on Escape", () => {
    expect(CANVAS_SOURCE).toContain('e.key === "Enter" && (trackPromptTool === "box" || trackPromptTool === "point")');
    expect(CANVAS_SOURCE).toContain("void commitTrackingProposal();");
    expect(CANVAS_SOURCE).toContain('e.key === "Escape" && trackPromptTool != null');
    expect(CANVAS_SOURCE).toContain("discardTrackingProposal();");
    expect(CANVAS_SOURCE).toContain('onDoubleClick={(e) => {');
  });

  it("awaits prediction and durable proposal commit before Save progress exits prompt mode", () => {
    const start = CANVAS_SOURCE.indexOf("const saveTrackProgress");
    const end = CANVAS_SOURCE.indexOf("const restoreTrackingHistory", start);
    const saveProgress = CANVAS_SOURCE.slice(start, end);
    expect(saveProgress).toContain("await prediction");
    expect(saveProgress).toContain("await commitTrackingProposal()");
    expect(saveProgress).toContain("await pendingSave");
    expect(saveProgress).toContain("setTrackPromptTool(null)");
    expect(saveProgress.indexOf("await commitTrackingProposal()")).toBeLessThan(saveProgress.indexOf("setTrackPromptTool(null)"));
  });

  it("commits, rather than discards, a live proposal when a paint tool is picked", () => {
    // Regression: switching Box/Point -> Brush ran `discardTrackingProposal`,
    // so the AI proposal vanished and every following brush stroke started
    // from the *previous* seed. Refining a box with the brush was impossible;
    // Save progress and Propagate both persisted the pre-box mask.
    const start = CANVAS_SOURCE.indexOf("const changeTrackPromptTool");
    const end = CANVAS_SOURCE.indexOf("const saveTrackProgress", start);
    const change = CANVAS_SOURCE.slice(start, end);
    expect(change).toContain("MANUAL_PROMPT_TOOLS.includes(tool)");
    expect(change).toContain("commitTrackingProposalRef.current()");
    // The commit branch must come first — the discard is the else.
    expect(change.indexOf("commitTrackingProposalRef.current()")).toBeLessThan(
      change.indexOf("discardTrackingProposal()"),
    );
    expect(CANVAS_SOURCE).toContain(
      'const MANUAL_PROMPT_TOOLS: readonly TrackingPromptTool[] = ["brush", "erase", "box_erase"]',
    );
  });

  it("flushes the proposal and the seed-save chain before propagating", () => {
    const start = CANVAS_SOURCE.indexOf("const propagateTrackingQueue");
    const end = CANVAS_SOURCE.indexOf("const trackingPromptKey", start);
    const propagate = CANVAS_SOURCE.slice(start, end);
    const commit = propagate.indexOf("await commitTrackingProposalRef.current()");
    const chain = propagate.indexOf("await trackPromptSaveChainRef.current");
    const request = propagate.indexOf("await trackTaskBatch(");
    expect(commit).toBeGreaterThanOrEqual(0);
    expect(chain).toBeGreaterThan(commit);
    expect(request).toBeGreaterThan(chain);
  });

  it("merges a committed proposal into the layer instead of replacing it", () => {
    // Regression: `saveTrackingPromptMask(proposal.mask)` made every commit the
    // layer's whole content, so committing a second Box/Point object deleted
    // the first and a layer could only ever hold one prompt.
    const start = CANVAS_SOURCE.indexOf("const commitTrackingProposal");
    const end = CANVAS_SOURCE.indexOf("commitTrackingProposalRef.current = commitTrackingProposal", start);
    const commit = CANVAS_SOURCE.slice(start, end);
    expect(commit).toContain("mergePromptMask(currentTrackingPromptMask(), proposal.mask)");
    expect(commit).toContain("saveTrackingPromptMask(merged.slice())");
    // The bare proposal must not reach the durable seed or the live draft.
    expect(commit).not.toContain("saveTrackingPromptMask(proposal.mask");
    expect(commit).not.toContain("mask: proposal.mask.slice()");
  });

  it("removes the destructive frontend Track split helper", () => {
    expect(CANVAS_SOURCE).not.toContain("splitTrackingSeed");
    expect(CANVAS_SOURCE).not.toContain("splitMask8");
    expect(CANVAS_SOURCE).not.toContain("onSplitCurrent");
  });
});
