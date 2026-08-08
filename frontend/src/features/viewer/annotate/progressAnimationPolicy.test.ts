import { describe, expect, it } from "vitest";

import STYLES_SOURCE_IMPORT from "../../../styles.css?raw";
import LABELS_3D_SOURCE_IMPORT from "../Labels3DPanel.tsx?raw";
import CHROME_SOURCE_IMPORT from "./AnnotateToolChrome.tsx?raw";
import TRACK_SOURCE_IMPORT from "./TrackRail.tsx?raw";

const STYLES: string = STYLES_SOURCE_IMPORT;
const LABELS_3D: string = LABELS_3D_SOURCE_IMPORT;
const CHROME: string = CHROME_SOURCE_IMPORT;
const TRACK: string = TRACK_SOURCE_IMPORT;

describe("progress animation policy", () => {
  it("keeps the only looping CSS animation scoped to Track propagation", () => {
    expect(STYLES.match(/\banimation:/g) ?? []).toHaveLength(1);
    expect(STYLES).toContain("animation: track-propagate-slide");
    expect(STYLES).not.toContain("labels-3d-progress");
    expect(STYLES).not.toContain("ai-busy");
  });

  it("renders a progressbar only for propagation", () => {
    expect(TRACK).toContain('role="progressbar" aria-label="Track propagation in progress"');
    expect(LABELS_3D).not.toContain('role="progressbar"');
    expect(CHROME).not.toContain('role="progressbar"');
  });

  it("does not drive 3D loading chrome with polling renders", () => {
    expect(LABELS_3D).not.toContain("setWaitedMs");
    expect(LABELS_3D).not.toContain("setBuildPct");
    expect(LABELS_3D).toContain('phase === "fetching"');
    expect(LABELS_3D).toContain('"Meshing on server…"');
  });
});
