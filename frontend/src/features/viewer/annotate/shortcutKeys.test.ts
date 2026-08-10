import { describe, expect, it } from "vitest";

import {
  hasToolModifier,
  isMacPlatform,
  shortcutModifierLabel,
  toolForShortcut,
} from "./shortcutKeys";

const key = (
  k: string,
  mods: Partial<{ ctrlKey: boolean; metaKey: boolean; altKey: boolean; shiftKey: boolean }> = {},
) => ({ key: k, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...mods });

const profile = { brush: "b", eraser: "e", delete: "", merge: "g", verify: "f", solo: "s" };

describe("platform modifier", () => {
  it("uses Cmd on macOS and Ctrl elsewhere", () => {
    expect(isMacPlatform({ platform: "MacIntel" })).toBe(true);
    expect(isMacPlatform({ platform: "Linux x86_64" })).toBe(false);
    expect(isMacPlatform({ userAgent: "Mozilla/5.0 (iPad; CPU OS 17_0)" })).toBe(true);
    expect(shortcutModifierLabel(true)).toBe("⌘");
    expect(shortcutModifierLabel(false)).toBe("Ctrl");
  });

  it("wants exactly its own modifier, not the other one", () => {
    expect(hasToolModifier(key("b", { metaKey: true }), true)).toBe(true);
    expect(hasToolModifier(key("b", { ctrlKey: true }), true)).toBe(false);
    expect(hasToolModifier(key("b", { ctrlKey: true }), false)).toBe(true);
    expect(hasToolModifier(key("b", { metaKey: true }), false)).toBe(false);
  });

  it("leaves Shift/Alt combinations alone", () => {
    // Cmd+Shift+B and Ctrl+Alt+B belong to the browser or the OS; claiming
    // them would be taking keys nobody bound here.
    expect(hasToolModifier(key("b", { metaKey: true, shiftKey: true }), true)).toBe(false);
    expect(hasToolModifier(key("b", { ctrlKey: true, altKey: true }), false)).toBe(false);
  });
});

describe("toolForShortcut", () => {
  it("resolves the bound tool for the platform modifier", () => {
    expect(toolForShortcut(key("b", { ctrlKey: true }), profile, false)).toBe("brush");
    expect(toolForShortcut(key("g", { metaKey: true }), profile, true)).toBe("merge");
  });

  it("resolves active-label Verify and Solo actions", () => {
    expect(toolForShortcut(key("f", { ctrlKey: true }), profile, false)).toBe("verify");
    expect(toolForShortcut(key("s", { metaKey: true }), profile, true)).toBe("solo");
  });

  it("is case-insensitive, so caps lock does not disarm it", () => {
    expect(toolForShortcut(key("B", { ctrlKey: true }), profile, false)).toBe("brush");
  });

  it("ignores an unmodified letter — those are the bare hotkeys", () => {
    expect(toolForShortcut(key("b"), profile, false)).toBeNull();
  });

  it("never matches a tool left deliberately unbound", () => {
    // Delete's letter is "", which must not match the empty-ish key of
    // anything — a reverse lookup on raw values would have.
    expect(toolForShortcut(key("", { ctrlKey: true }), profile, false)).toBeNull();
    expect(toolForShortcut(key("Delete", { ctrlKey: true }), profile, false)).toBeNull();
  });

  it("returns null for a letter nothing is bound to", () => {
    expect(toolForShortcut(key("z", { ctrlKey: true }), profile, false)).toBeNull();
  });

  it("returns null when the account has no shortcut map yet", () => {
    expect(toolForShortcut(key("b", { ctrlKey: true }), null, false)).toBeNull();
  });

  it("resolves the current account map without cross-talk from another account", () => {
    const accountA = { ...profile, brush: "n" };
    const accountB = { ...profile, brush: "q" };
    expect(toolForShortcut(key("n", { ctrlKey: true }), accountA, false)).toBe("brush");
    expect(toolForShortcut(key("n", { ctrlKey: true }), accountB, false)).toBeNull();
    expect(toolForShortcut(key("q", { ctrlKey: true }), accountB, false)).toBe("brush");
  });
});
