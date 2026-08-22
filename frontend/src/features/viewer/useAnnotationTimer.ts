import { useEffect, useRef, useState } from "react";
import {
  heartbeatTaskTiming,
  startTaskTiming,
  type TimingStatus,
} from "../../api/timing";

/**
 * Drives automatic annotation timing for the editable editor.
 *
 * The protocol is deliberately lopsided: the **server** decides whether this
 * task is timed at all, how long a heartbeat is worth, and when a session has
 * been abandoned. This hook only says "I am still here", on the cadence the
 * server hands back, and stops saying it when the annotator is plainly not
 * working. It cannot propose a duration, and a request that tried to would be
 * ignored.
 *
 * What stops the clock, and why each one is needed:
 *
 * * **Leaving the editor** — route change or unmount. The cleanup fires a
 *   best-effort stop.
 * * **Closing the tab or window** — `pagehide`, via `sendBeacon`, which is the
 *   only request the browser reliably lets a dying page make. `beforeunload` is
 *   not trusted here: it does not fire on mobile, on crash, or on force-quit,
 *   which is exactly why the server caps abandoned sessions independently.
 * * **The tab being hidden** for longer than the server's grace — a tab left
 *   open on another desktop all day must not bank a day.
 * * **The annotator going idle** — no pointer, key or wheel activity for the
 *   configured window. Any ordinary annotation interaction resumes it
 *   immediately, and resuming is a plain start call, so nothing is lost.
 * * **Losing the assignment or the lock** — the server refuses the next
 *   heartbeat, and the hook simply stops.
 *
 * Nothing here is allowed to disturb annotation. Every request is
 * fire-and-forget: a timing outage means "we stopped measuring", never "your
 * editor is broken", so failures are swallowed rather than surfaced as errors.
 */

/** A per-tab id, stable across reloads of that tab, so a refresh resumes the
 *  same session instead of opening a second one. `sessionStorage` is per-tab by
 *  definition, which is precisely the scope wanted: a second tab is a second
 *  client, and the server unions their overlap rather than trusting either. */
function tabToken(taskId: number): string {
  const key = `mito.timing.tab.${taskId}`;
  try {
    const existing = window.sessionStorage.getItem(key);
    if (existing) return existing;
    const fresh =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `t-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    window.sessionStorage.setItem(key, fresh);
    return fresh;
  } catch {
    // Private mode, disabled storage, or a sandboxed frame. A per-mount token
    // still works; it just cannot survive a reload, and the server's idle
    // handling absorbs the difference.
    return `t-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

const ACTIVITY_EVENTS = [
  "pointerdown",
  "pointermove",
  "keydown",
  "wheel",
] as const;

export interface AnnotationTimerState {
  /** Server-reported cumulative time for the task, in seconds. */
  totalSeconds: number | null;
  /** True while a session is open and accruing. */
  tracking: boolean;
  /** Is this volume measured at all? False means legacy-exempt. */
  eligible: boolean;
  /** Local optimistic seconds since the last server answer, for a live display
   *  that does not need a request per second. The server stays authoritative:
   *  every heartbeat replaces this with the real number. */
  liveSeconds: number | null;
}

/**
 * @param taskId   the task being edited
 * @param enabled  only ever true for the *editable* editor. A read-only viewer
 *                 and the Details page pass false and never start a clock.
 */
export function useAnnotationTimer(
  taskId: number,
  enabled: boolean,
): AnnotationTimerState {
  const [status, setStatus] = useState<TimingStatus | null>(null);
  const [drift, setDrift] = useState(0);

  // Refs, not state: the loops below must read the newest value without
  // re-subscribing, and a re-render per heartbeat would be absurd.
  const sessionRef = useRef<string | null>(null);
  const lastActivityRef = useRef<number>(Date.now());
  const hiddenSinceRef = useRef<number | null>(null);
  const stoppedRef = useRef(false);
  const configRef = useRef<TimingStatus["config"] | null>(null);
  const taskRef = useRef(taskId);
  taskRef.current = taskId;

  useEffect(() => {
    if (!enabled) {
      setStatus(null);
      setDrift(0);
      sessionRef.current = null;
      return;
    }

    let cancelled = false;
    let timer: number | undefined;
    const token = tabToken(taskId);
    lastActivityRef.current = Date.now();
    hiddenSinceRef.current = document.hidden ? Date.now() : null;
    stoppedRef.current = false;

    const noteActivity = () => {
      lastActivityRef.current = Date.now();
    };

    const apply = (next: TimingStatus) => {
      if (cancelled) return;
      configRef.current = next.config;
      sessionRef.current = next.tracking ? next.session_id : null;
      setStatus(next);
      setDrift(0);
    };

    /** Open or resume this tab's session. Idempotent on the token, so calling
     *  it again after an idle pause resumes rather than duplicating. */
    const begin = async () => {
      try {
        apply(await startTaskTiming(taskRef.current, token));
      } catch {
        // Fire-and-forget by design: annotation continues unmeasured.
      }
    };

    const beat = async () => {
      const session = sessionRef.current;
      if (!session) return;
      try {
        apply(await heartbeatTaskTiming(taskRef.current, session));
      } catch {
        /* ignored — see the module docstring */
      }
    };

    /** Best-effort close. Uses `sendBeacon` when the page is going away,
     *  because a normal fetch is cancelled mid-flight by unload. */
    const finish = (reason: string, { beacon = false } = {}) => {
      const session = sessionRef.current;
      if (!session) return;
      sessionRef.current = null;
      const url = `/api/tasks/${taskRef.current}/timing/stop/`;
      const body = JSON.stringify({ session_id: session, reason });
      try {
        if (beacon && typeof navigator !== "undefined" && navigator.sendBeacon) {
          navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
          return;
        }
        void fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
          credentials: "include",
          keepalive: true,
        }).catch(() => {});
      } catch {
        /* the server's abandonment cap is the backstop for all of this */
      }
    };

    const tick = () => {
      const config = configRef.current;
      const idleMs = (config?.idle_seconds ?? 120) * 1000;
      const hiddenMs = (config?.hidden_grace_seconds ?? 60) * 1000;
      const now = Date.now();

      const hiddenTooLong =
        hiddenSinceRef.current !== null && now - hiddenSinceRef.current > hiddenMs;
      const idleTooLong = now - lastActivityRef.current > idleMs;

      if (hiddenTooLong || idleTooLong) {
        // Pause. The server credits nothing past the last heartbeat, so simply
        // ceasing to beat is the whole mechanism — there is no "paused" state
        // to get out of sync.
        if (!stoppedRef.current) {
          stoppedRef.current = true;
          finish(hiddenTooLong ? "hidden" : "idle");
          setStatus((current) =>
            current ? { ...current, tracking: false } : current,
          );
        }
        return;
      }
      if (stoppedRef.current) {
        stoppedRef.current = false;
        void begin();
        return;
      }
      void beat();
    };

    const onVisibility = () => {
      if (document.hidden) {
        hiddenSinceRef.current = Date.now();
        return;
      }
      hiddenSinceRef.current = null;
      noteActivity();
      if (stoppedRef.current) {
        stoppedRef.current = false;
        void begin();
      }
    };

    const onPageHide = () => finish("unload", { beacon: true });

    void begin().then(() => {
      if (cancelled) return;
      const seconds = configRef.current?.heartbeat_seconds ?? 30;
      timer = window.setInterval(tick, Math.max(5, seconds) * 1000);
    });

    for (const event of ACTIVITY_EVENTS) {
      window.addEventListener(event, noteActivity, { passive: true });
    }
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onPageHide);

    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearInterval(timer);
      for (const event of ACTIVITY_EVENTS) {
        window.removeEventListener(event, noteActivity);
      }
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onPageHide);
      // Leaving the editor, navigating to another task, or logging out all
      // land here.
      finish("left_editor");
    };
  }, [enabled, taskId]);

  // A once-a-second local tick so the displayed duration moves between server
  // answers. Purely cosmetic — every heartbeat overwrites it with the truth.
  useEffect(() => {
    if (!status?.tracking) return;
    const timer = window.setInterval(() => setDrift((d) => d + 1), 1000);
    return () => window.clearInterval(timer);
  }, [status?.tracking, status?.total_seconds]);

  return {
    totalSeconds: status?.total_seconds ?? null,
    tracking: Boolean(status?.tracking),
    eligible: Boolean(status?.eligible),
    liveSeconds:
      status?.total_seconds == null ? null : status.total_seconds + drift,
  };
}
