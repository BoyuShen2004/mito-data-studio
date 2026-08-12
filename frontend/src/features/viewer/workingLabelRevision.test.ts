import { describe, expect, it } from "vitest";
import { labelIdsForCache } from "./workingLabelRevision";

describe("working-label revision caching", () => {
  it("keeps reusable plane pixels but drops the volume-wide revision", () => {
    const response = {
      shape: [2, 2] as [number, number],
      runs: [[7, 4]] as [number, number][],
      revision: "old-volume-revision",
    };

    expect(labelIdsForCache(response)).toEqual({
      shape: [2, 2],
      runs: [[7, 4]],
    });
    // The live network response still carries the authoritative token for the
    // caller that initiated the read; only future cache hits are revisionless.
    expect(response.revision).toBe("old-volume-revision");
  });
});
