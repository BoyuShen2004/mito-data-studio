import { describe, expect, it } from "vitest";
// Vite's `?raw` rather than node:fs — this project has no @types/node, and
// `npm run build` typechecks the test tree.
import css from "../../styles.css?raw";

/**
 * Layout regressions are invisible to jsdom — it computes no geometry — so the
 * rules that keep these controls from colliding are asserted against the
 * stylesheet itself. Every assertion here stands for a bug that shipped:
 *
 *  - an absolutely centred Region only painted over "Stop sharing";
 *  - a Share cluster that grew when sharing was switched on, shoving the bar;
 *  - a bare `<span>` around Cursor + `<select>`, which stacked the label above
 *    the dropdown;
 *  - a size readout that collided with the Cursor control (now removed).
 *
 * The principle they encode: every slot reserves its worst case up front.
 */

/** The declarations of the first rule whose selector list matches exactly. */
function rule(selector: string): string {
  const wanted = selector.replace(/\s+/g, " ").trim();
  for (const [, head, body] of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    if (head.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s+/g, " ").trim() === wanted) {
      return body;
    }
  }
  throw new Error(`no rule for ${selector}`);
}

describe("editor topbar slots", () => {
  it("keeps the centre slot in flow, with a box of its own", () => {
    const centre = rule(".editor-center-slot");
    // The overlap bug in one line: no absolute positioning, so the slot cannot
    // paint over a sibling, and needs no z-index to sit above one.
    expect(centre).not.toMatch(/position:\s*absolute/);
    expect(centre).not.toMatch(/z-index/);
    expect(centre).not.toMatch(/translateX/);
    expect(centre).toMatch(/flex:\s*0 0 auto/);
    // Centred in the gap the side slots leave, by auto margins — still an
    // in-flow flex item, so it displaces its neighbours instead of covering
    // them however narrow the window gets.
    expect(rule(".editor-center-slot:not(:empty)")).toMatch(/margin-inline:\s*auto/);
  });

  it("never lets a side slot shrink below the children it holds", () => {
    const sides = rule(".editor-left-slot, .editor-right-slot");
    expect(sides).toMatch(/flex:\s*0 1 auto/);
    // `min-width: 0` here is what let the Share buttons spill out of their
    // track and back over Region only at ~1280px. The flex default (`auto`)
    // floors each track at its own content.
    expect(sides).not.toMatch(/min-width:\s*0/);
  });

  it("scrolls the bar rather than wrapping or clipping when it cannot fit", () => {
    const bar = rule(".editor-topbar");
    expect(bar).toMatch(/overflow-x:\s*auto/);
    expect(bar).toMatch(/flex-wrap:\s*nowrap/);
  });

  it("reserves Share's widest state so sharing never moves the bar", () => {
    const share = rule(".editor-share-slot");
    // A floor, so idle "Share" and expanded "Copy link / Stop sharing" both
    // round up to one box — and an underestimate widens the slot rather than
    // spilling buttons across the centre one.
    expect(share).toMatch(/min-width:\s*14rem/);
    expect(share).toMatch(/flex:\s*0 0 auto/);
    // An error message inside it must not be able to widen it either.
    expect(rule(".editor-share-slot .share-control .error")).toMatch(
      /text-overflow:\s*ellipsis/,
    );
  });

  it("reserves one width for every Submit label", () => {
    // "Submit for review" -> "Submit again" -> "Submitting…" are the same
    // control at three points in a task's life. Sizing to the content moved
    // Share (and the rest of the left slot) every time the string changed.
    const submit = rule(".editor-submit-btn");
    expect(submit).toMatch(/width:\s*9\.5rem/);
    expect(submit).toMatch(/min-width:\s*9\.5rem/);
    expect(submit).toMatch(/max-width:\s*9\.5rem/);
    expect(submit).toMatch(/flex:\s*0 0 auto/);
    expect(submit).toMatch(/white-space:\s*nowrap/);
  });

  it("never relocates a slot with a media query", () => {
    // The old fallback moved Region only into the flow below 1100px, which is
    // "detect collision, then move" — the thing reserved slots exist to avoid.
    const insideMediaBlocks = [...css.matchAll(/@media[^{]*\{((?:[^{}]|\{[^}]*\})*)\}/g)]
      .map((m) => m[1])
      .join("\n");
    expect(insideMediaBlocks).not.toContain(".editor-center-slot");
  });

  it("lets only the volume title absorb a narrow window", () => {
    expect(rule(".editor-topbar-meta")).toMatch(/text-overflow:\s*ellipsis/);
    expect(rule(".editor-topbar")).toMatch(/flex-wrap:\s*nowrap/);
  });
});

describe("brush/erase context row", () => {
  it("keeps Cursor and its dropdown on one line", () => {
    const control = rule(".tool-context > .tool-cursor-control");
    expect(control).toMatch(/display:\s*inline-flex/);
    expect(control).toMatch(/white-space:\s*nowrap/);
    expect(control).toMatch(/align-items:\s*center/);
  });

  it("reserves the dropdown's width for the longest style name", () => {
    const select = rule(
      ".tool-context .tool-cursor-control > select.tool-cursor-select",
    );
    expect(select).toMatch(/width:\s*7rem/);
    expect(select).toMatch(/min-width:\s*7rem/);
    expect(select).toMatch(/max-width:\s*7rem/);
  });

  it("does not reserve space for a removed numeric size readout", () => {
    expect(css).not.toContain(".brush-size-readout");
  });

  it("pins the slider, the only other control before the dropdown", () => {
    expect(rule('.tool-context input[type="range"]')).toMatch(/width:\s*8rem/);
  });
});
