import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/** Below this many rows, render the list normally — virtualization only earns
 * its complexity on the lists that are actually big. */
export const VIRTUALIZE_ABOVE = 200;
/** Rows kept rendered beyond the viewport so a fast scroll doesn't flash blank. */
const OVERSCAN = 8;
const FALLBACK_ROW_HEIGHT = 26;

export interface VirtualWindow {
  /** True when the list is long enough that windowing is on. */
  enabled: boolean;
  /** Render rows in `[start, end)`. */
  start: number;
  end: number;
  /** Spacer heights (px) standing in for the rows above/below the window. */
  padTop: number;
  padBottom: number;
  rowHeight: number;
  /**
   * Scroll a row index into view — works whether or not it is rendered.
   * `offsetTop` leaves room for a sticky ceiling inside the scrollport
   * (Labels count / 3D all) so the row lands on the first *visible* line.
   */
  scrollToIndex: (index: number, opts?: { offsetTop?: number }) => void;
}

/**
 * Windowed rendering for a long list inside an already-scrolling container.
 *
 * The Labels panel's "All" scope is one row per instance in the volume, and a
 * real EM volume here has ~5,700 of them — six-plus DOM nodes each, re-rendered
 * on every keystroke in the filter box. This keeps the DOM proportional to what
 * is on screen instead.
 *
 * `containerRef` must be `position: relative` so `listRef.offsetTop` is stable
 * against `scrollTop` (unlike getBoundingClientRect + scrollTop, which drifts).
 * Rows are uniform height when `enabled` (see `.labels-list-virtual`).
 */
export function useVirtualRows(
  count: number,
  containerRef: React.RefObject<HTMLElement | null>,
  listRef: React.RefObject<HTMLElement | null>,
): VirtualWindow {
  const enabled = count > VIRTUALIZE_ABOVE;
  const [viewport, setViewport] = useState({ scrollTop: 0, height: 0 });
  const rowHeightRef = useRef(FALLBACK_ROW_HEIGHT);

  const measure = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    setViewport((prev) =>
      prev.scrollTop === el.scrollTop && prev.height === el.clientHeight
        ? prev
        : { scrollTop: el.scrollTop, height: el.clientHeight },
    );
  }, [containerRef]);

  useEffect(() => {
    if (!enabled) return;
    const el = containerRef.current;
    if (!el) return;
    measure();
    el.addEventListener("scroll", measure, { passive: true });
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", measure);
      ro.disconnect();
    };
  }, [enabled, containerRef, measure]);

  // Learn the real row height from the first rendered row (font size and
  // padding are CSS, not something this hook should hard-code).
  useLayoutEffect(() => {
    if (!enabled) return;
    const first = listRef.current?.querySelector<HTMLElement>("li[data-row]");
    if (first && first.offsetHeight > 0) rowHeightRef.current = first.offsetHeight;
  });

  const rowHeight = rowHeightRef.current || FALLBACK_ROW_HEIGHT;
  // offsetTop (with a positioned scrollport) does not include scrollTop — unlike
  // getBoundingClientRect().top + scrollTop, which collapses to scrollTop in
  // jsdom and briefly mis-windows a far pin in the browser.
  const listTop = enabled ? listRef.current?.offsetTop ?? 0 : 0;

  let start = 0;
  let end = count;
  if (enabled) {
    const above = Math.max(0, viewport.scrollTop - listTop);
    const visibleRows = Math.ceil(Math.max(viewport.height, 1) / rowHeight);
    start = Math.max(0, Math.floor(above / rowHeight) - OVERSCAN);
    end = Math.min(count, start + visibleRows + OVERSCAN * 2);
  }

  const scrollToIndex = useCallback(
    (index: number, opts?: { offsetTop?: number }) => {
      const el = containerRef.current;
      if (!el) return;
      const ceiling = Math.max(0, opts?.offsetTop ?? 0);
      const h = rowHeightRef.current || FALLBACK_ROW_HEIGHT;
      const top = (listRef.current?.offsetTop ?? 0) + index * h;
      el.scrollTop = Math.max(0, top - ceiling);
      // Sync the window in this turn. A far jump from scrollTop=0 used to leave
      // padTop stale until the next scroll event; the spacer rewrite then
      // knocked the pin askew so only a second click looked right.
      setViewport((prev) =>
        prev.scrollTop === el.scrollTop && prev.height === el.clientHeight
          ? prev
          : { scrollTop: el.scrollTop, height: el.clientHeight },
      );
    },
    [containerRef, listRef],
  );

  return {
    enabled,
    start,
    end,
    padTop: enabled ? start * rowHeight : 0,
    padBottom: enabled ? Math.max(0, count - end) * rowHeight : 0,
    rowHeight,
    scrollToIndex,
  };
}
