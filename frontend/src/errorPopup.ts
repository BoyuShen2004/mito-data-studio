/** The one way the app reports a failure: a native popup, shown once.
 *
 * A single failure often reaches the user through several paths at once — each
 * click of a double-click re-predicts, Enter predicts again, a save and the
 * action that awaited it both report. A message the user dismissed moments ago
 * (or one it is already part of) is therefore dropped instead of popping again.
 */
const REPEAT_WINDOW_MS = 3000;

let last = { text: "", closedAt: 0 };

export function showError(text: string): void {
  if (last.text.includes(text) && Date.now() - last.closedAt < REPEAT_WINDOW_MS) return;
  window.alert(text);
  // Measured after the popup closes: repeats queued behind it land right away.
  last = { text, closedAt: Date.now() };
}

/** Test isolation only. */
export function resetShowError(): void {
  last = { text: "", closedAt: 0 };
}
