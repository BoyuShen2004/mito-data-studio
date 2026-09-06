import { describe, expect, it } from "vitest";
import { EMPTY_VALUE } from "./emptyValue";
import { durationTitle, formatDuration, preciseDuration, relativeTime } from "./time";

/**
 * The same table `annotation.test_time_tracking.test_format_duration_is_compact_
 * at_every_scale` asserts on the server. Both sides render these durations, and
 * the only way they cannot drift is for both to be pinned to one list.
 */
const CASES: Array<[number | null, string]> = [
  [null, "—"],
  [0, "0m"],
  [59, "0m"],
  [60, "1m"],
  [2220, "37m"],
  [3600, "1h"],
  [8040, "2h 14m"],
  [86400, "1d"],
  [273600, "3d 4h"],
];

describe("formatDuration", () => {
  it.each(CASES)("formats %s as %s", (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected);
  });

  it("treats unknown and zero as different answers", () => {
    // The whole point of the legacy exemption: `-` says "we do not know",
    // `0m` says "we measured, and it was nothing".
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(0)).toBe("0m");
    expect(formatDuration(null)).not.toBe(formatDuration(0));
  });

  it("treats undefined as unknown too", () => {
    expect(formatDuration(undefined)).toBe("—");
  });

  it("never renders a negative duration", () => {
    expect(formatDuration(-500)).toBe("0m");
  });

  it("truncates rather than rounding up", () => {
    // 119 s is one minute of work, not two.
    expect(formatDuration(119)).toBe("1m");
  });
});

describe("preciseDuration", () => {
  it("spells out the exact value for a tooltip", () => {
    expect(preciseDuration(8049)).toBe("2 h 14 m 9 s");
    expect(preciseDuration(45)).toBe("45 s");
    expect(preciseDuration(0)).toBe("0 s");
  });

  it("explains the unknown rather than showing a number", () => {
    expect(preciseDuration(null)).toMatch(/before time tracking/);
  });
});

describe("durationTitle", () => {
  it("explains a legacy volume instead of quoting a total", () => {
    expect(durationTitle(0, { legacy: true })).toMatch(/before time tracking/);
    expect(durationTitle(null)).toMatch(/before time tracking/);
  });

  it("quotes the measured value otherwise", () => {
    expect(durationTitle(90)).toBe("Measured annotation time: 1 m 30 s");
  });
});

describe("relativeTime", () => {
  const now = new Date("2026-09-06T12:00:00Z");
  const ago = (ms: number) => new Date(now.getTime() - ms).toISOString();

  it("names the bucket the event falls in", () => {
    expect(relativeTime(ago(30_000), now)).toBe("just now");
    expect(relativeTime(ago(60_000), now)).toBe("1 minute ago");
    expect(relativeTime(ago(45 * 60_000), now)).toBe("45 minutes ago");
    expect(relativeTime(ago(3 * 3_600_000), now)).toBe("3 hours ago");
    expect(relativeTime(ago(2 * 86_400_000), now)).toBe("2 days ago");
  });

  it("falls back to a date once the event is old, or in the future", () => {
    expect(relativeTime(ago(400 * 86_400_000), now)).toMatch(/\d/);
    expect(relativeTime(ago(400 * 86_400_000), now)).not.toMatch(/ago/);
    expect(relativeTime(new Date(now.getTime() + 60_000).toISOString(), now)).not.toMatch(/ago/);
  });

  it("renders a missing or unparseable timestamp as the empty value, never as now", () => {
    expect(relativeTime(null, now)).toBe(EMPTY_VALUE);
    expect(relativeTime("", now)).toBe(EMPTY_VALUE);
    expect(relativeTime("not a date", now)).toBe(EMPTY_VALUE);
  });
});
