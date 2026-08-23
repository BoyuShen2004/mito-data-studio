import { describe, expect, it } from "vitest";

import { mergePromptMask, trackingSeedOverlays } from "./promptMask";

/** Grid helper: "." empty, "#" seeded. */
function mask(rows: string[]): Uint8Array {
  return Uint8Array.from(rows.join("").split(""), (c) => (c === "#" ? 1 : 0));
}

function render(m: Uint8Array, width: number): string[] {
  const rows: string[] = [];
  for (let y = 0; y < m.length / width; y += 1) {
    rows.push(Array.from(m.slice(y * width, (y + 1) * width), (v) => (v ? "#" : ".")).join(""));
  }
  return rows;
}

/**
 * 8-connected component count, mirroring the backend's `split_components`.
 *
 * The point of merging is that the backend can still tell the blobs apart, so
 * the tests assert the component count the server would see, not just that some
 * pixels survived.
 */
function componentCount(m: Uint8Array, width: number): number {
  const seen = new Uint8Array(m.length);
  const height = m.length / width;
  let count = 0;
  for (let start = 0; start < m.length; start += 1) {
    if (!m[start] || seen[start]) continue;
    count += 1;
    const stack = [start];
    seen[start] = 1;
    while (stack.length) {
      const at = stack.pop()!;
      const y = Math.floor(at / width);
      const x = at % width;
      for (let dy = -1; dy <= 1; dy += 1) {
        for (let dx = -1; dx <= 1; dx += 1) {
          const ny = y + dy;
          const nx = x + dx;
          if (ny < 0 || nx < 0 || ny >= height || nx >= width) continue;
          const next = ny * width + nx;
          if (!m[next] || seen[next]) continue;
          seen[next] = 1;
          stack.push(next);
        }
      }
    }
  }
  return count;
}

describe("mergePromptMask", () => {
  it("keeps both objects when a second proposal is committed on the same layer", () => {
    // Regression: committing a Box/Point proposal replaced the layer mask, so
    // the first object vanished the moment a second one was committed and a
    // layer could never hold more than one prompt.
    const first = mask([
      "##....",
      "##....",
      "......",
      "......",
    ]);
    const second = mask([
      "......",
      "......",
      "....##",
      "....##",
    ]);
    const merged = mergePromptMask(first, second);
    expect(render(merged, 6)).toEqual([
      "##....",
      "##....",
      "....##",
      "....##",
    ]);
    // Two separated blobs are exactly how two tracked objects are requested.
    expect(componentCount(merged, 6)).toBe(2);
  });

  it("accumulates a third object without disturbing the first two", () => {
    const width = 7;
    let layer = mask([
      "##.....",
      "##.....",
      ".......",
      ".......",
      ".......",
    ]);
    layer = mergePromptMask(layer, mask([
      ".......",
      "....##.",
      "....##.",
      ".......",
      ".......",
    ]));
    layer = mergePromptMask(layer, mask([
      ".......",
      ".......",
      ".......",
      ".......",
      "#......",
    ]));
    expect(componentCount(layer, width)).toBe(3);
    expect(render(layer, width)).toEqual([
      "##.....",
      "##..##.",
      "....##.",
      ".......",
      "#......",
    ]);
  });

  it("fuses objects that actually touch into one component", () => {
    // Overlap is not an error to guard against — the annotator drew one thing
    // in two gestures, and one component is the honest reading.
    const merged = mergePromptMask(
      mask(["##..", "##..", "....", "...."]),
      mask(["....", ".##.", ".##.", "...."]),
    );
    expect(componentCount(merged, 4)).toBe(1);
  });

  it("returns the proposal unchanged when the layer is still empty", () => {
    const proposal = mask(["#.", ".#"]);
    expect(Array.from(mergePromptMask(null, proposal))).toEqual([1, 0, 0, 1]);
    expect(Array.from(mergePromptMask(new Uint8Array(4), proposal))).toEqual([1, 0, 0, 1]);
  });

  it("never mutates either input", () => {
    const base = mask(["#.", ".."]);
    const addition = mask(["..", ".#"]);
    mergePromptMask(base, addition);
    expect(Array.from(base)).toEqual([1, 0, 0, 0]);
    expect(Array.from(addition)).toEqual([0, 0, 0, 1]);
  });

  it("prefers the fresh proposal when the layer shape changed underneath it", () => {
    // Unioning masks of different shapes would smear pixels across rows that
    // no longer correspond; the prediction that matches the current slice wins.
    const stale = new Uint8Array(9).fill(1);
    const proposal = mask(["#.", ".."]);
    expect(Array.from(mergePromptMask(stale, proposal))).toEqual([1, 0, 0, 0]);
  });
});

describe("trackingSeedOverlays", () => {
  const seed = (z: number, rle: [number, number][]) => ({ z, rle, shape: [2, 2] as [number, number] });
  const args = { z: 0, height: 2, width: 2 };

  it("draws a class whose slots the selection index does not name", () => {
    // Regression: the overlay decided what to paint by matching a "selected
    // slot" index. A class whose slot list was empty — or whose index was stale
    // — matched nothing, so its live strokes were never drawn and the annotator
    // painted onto a canvas that showed nothing back.
    const live = Uint8Array.from([1, 1, 0, 0]);
    const overlays = trackingSeedOverlays({
      ...args,
      prompts: [{ parent_id: 9, subclasses: [] }],
      selectedParentId: 9,
      selectedMask: live,
    });
    expect(overlays).toHaveLength(1);
    expect(overlays[0].mask).toBe(live);
    expect(overlays[0].emphasis).toBe(2);
  });

  it("draws the selected class once, however many slots it carries", () => {
    const live = Uint8Array.from([1, 1, 1, 0]);
    const overlays = trackingSeedOverlays({
      ...args,
      prompts: [{
        parent_id: 9,
        subclasses: [
          { index: 1, seeds: [seed(0, [[0, 2]])] },
          { index: 2, seeds: [seed(0, [[2, 1]])] },
        ],
      }],
      selectedParentId: 9,
      selectedMask: live,
    });
    // One overlay from the live surface, not one per slot — stacking them would
    // composite the same pixels twice and darken the selected seed.
    expect(overlays).toHaveLength(1);
    expect(overlays[0].mask).toBe(live);
  });

  it("still draws every other queued class from its durable seeds", () => {
    const overlays = trackingSeedOverlays({
      ...args,
      prompts: [
        { parent_id: 9, subclasses: [{ index: 1, seeds: [seed(0, [[0, 1]])] }] },
        { parent_id: 12, subclasses: [{ index: 1, seeds: [seed(0, [[3, 1]])] }] },
      ],
      selectedParentId: 9,
      selectedMask: Uint8Array.from([1, 0, 0, 0]),
    });
    expect(overlays.map((o) => o.parentId).sort((a, b) => a - b)).toEqual([9, 12]);
    // The edited class paints last so it stays legible over the rest.
    expect(overlays[overlays.length - 1].parentId).toBe(9);
    expect(overlays[overlays.length - 1].emphasis).toBe(2);
  });

  it("omits the selected class when its surface is empty", () => {
    const overlays = trackingSeedOverlays({
      ...args,
      prompts: [{ parent_id: 9, subclasses: [{ index: 1, seeds: [] }] }],
      selectedParentId: 9,
      selectedMask: new Uint8Array(4),
    });
    expect(overlays).toEqual([]);
  });

  it("skips seeds saved at a different slice shape", () => {
    const overlays = trackingSeedOverlays({
      ...args,
      prompts: [{
        parent_id: 12,
        subclasses: [{ index: 1, seeds: [{ z: 0, rle: [[0, 1]], shape: [8, 8] as [number, number] }] }],
      }],
      selectedParentId: 9,
      selectedMask: null,
    });
    expect(overlays).toEqual([]);
  });

  it("only draws seeds belonging to the layer on screen", () => {
    const overlays = trackingSeedOverlays({
      ...args,
      prompts: [{ parent_id: 12, subclasses: [{ index: 1, seeds: [seed(3, [[0, 1]])] }] }],
      selectedParentId: null,
      selectedMask: null,
    });
    expect(overlays).toEqual([]);
  });
});
