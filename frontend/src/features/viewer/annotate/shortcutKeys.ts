/**
 * Cmd/Ctrl + letter tool shortcuts, shared by the profile editor (which shows
 * them) and the canvas (which fires them).
 *
 * The modifier is *derived*, never stored: the same account is used from a Mac
 * and a Linux workstation, and each expects its own convention. Storing
 * "cmd+b" in the profile would make one of those two wrong.
 *
 * These bindings sit on top of the editor's existing bare-letter hotkeys rather
 * than replacing them: an annotator who already types `B` for Brush keeps doing
 * that, and the modified form is what is customisable.
 */

import type { PaintTool } from "./paintTools";

export type AnnotateShortcutAction = PaintTool | "verify" | "solo";

/** True on macOS, where the tool modifier is Cmd rather than Ctrl. */
export function isMacPlatform(
  nav: { platform?: string; userAgent?: string } | undefined =
    typeof navigator === "undefined" ? undefined : navigator,
): boolean {
  const hint = `${nav?.platform ?? ""} ${nav?.userAgent ?? ""}`;
  return /mac|iphone|ipad|ipod/i.test(hint);
}

/** What to print in the UI for the modifier this machine uses. */
export function shortcutModifierLabel(mac = isMacPlatform()): string {
  return mac ? "⌘" : "Ctrl";
}

/** The modifier this machine's shortcuts require, as a keyboard-event flag. */
export function hasToolModifier(
  event: { ctrlKey: boolean; metaKey: boolean; altKey: boolean; shiftKey: boolean },
  mac = isMacPlatform(),
): boolean {
  // Exactly one modifier: Cmd+Shift+B and Ctrl+Alt+B are other people's
  // shortcuts, and claiming them would be taking keys nobody bound here.
  if (event.altKey || event.shiftKey) return false;
  return mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
}

/**
 * Which tool a keydown selects, or `null` for "not one of ours".
 *
 * `shortcuts` is the profile map (`{tool: letter}`); an empty letter means the
 * tool is deliberately unbound and must never match, which is why this cannot
 * just be a reverse lookup on the raw values.
 */
export function toolForShortcut(
  event: {
    key: string;
    ctrlKey: boolean;
    metaKey: boolean;
    altKey: boolean;
    shiftKey: boolean;
  },
  shortcuts: Record<string, string> | null | undefined,
  mac = isMacPlatform(),
): AnnotateShortcutAction | null {
  if (!shortcuts || !hasToolModifier(event, mac)) return null;
  const pressed = event.key.toLowerCase();
  if (pressed.length !== 1) return null;
  for (const [tool, letter] of Object.entries(shortcuts)) {
    if (letter && letter.toLowerCase() === pressed) return tool as AnnotateShortcutAction;
  }
  return null;
}
