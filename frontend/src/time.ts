/**
 * Compact duration formatting for annotation time.
 *
 * Mirrors `annotation.timing.format_duration` on the server, case for case —
 * `frontend/src/time.test.ts` and `annotation/test_time_tracking.py` assert the
 * same table, so the two cannot drift into disagreeing about what `2h 14m`
 * means.
 *
 * The important distinction this encodes: **`null` is not zero.** A
 * legacy-exempt volume's annotation began before the feature existed, so its
 * real total is unknowable and renders as `-`. A volume that is measured but
 * has never been opened renders as `0m`. Collapsing the two would quietly
 * claim we know something we do not.
 */

/** `-` for unknown, `0m`, `37m`, `2h 14m`, `3d 4h`. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "-";
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  if (hours < 24) return restMinutes ? `${hours}h ${restMinutes}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours ? `${days}d ${restHours}h` : `${days}d`;
}

/** The long form for a `title=` tooltip: `2 h 14 m 09 s`, or the honest unknown. */
export function preciseDuration(seconds: number | null | undefined): string {
  if (seconds == null) {
    return "Annotation started before time tracking — the real total is unknown.";
  }
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const parts: string[] = [];
  if (h) parts.push(`${h} h`);
  if (m || h) parts.push(`${m} m`);
  parts.push(`${s} s`);
  return parts.join(" ");
}

/** What a measured/unmeasured pair renders as. Server sends both; this is the
 * fallback for surfaces that only have the number. */
export const durationTitle = (
  seconds: number | null | undefined,
  { legacy = false }: { legacy?: boolean } = {},
): string =>
  legacy || seconds == null
    ? "Annotation started before time tracking — the real total is unknown."
    : `Measured annotation time: ${preciseDuration(seconds)}`;
