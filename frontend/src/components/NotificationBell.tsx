import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { fetchUnreadCount } from "../api/notifications";

/** How often the badge re-checks. Long enough that a always-open editor tab
 * costs almost nothing, short enough that a review decision shows up before
 * the annotator wonders. */
const POLL_MS = 60_000;

/**
 * The unread badge in the global navbar.
 *
 * Renders as a plain link with no badge when the count is zero or the feature
 * is disabled — `fetchUnreadCount` resolves to 0 rather than rejecting, so a
 * deployment without notifications shows an ordinary Inbox link instead of an
 * error over the whole authenticated app.
 */
export default function NotificationBell() {
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    let alive = true;
    const check = () => {
      fetchUnreadCount().then((count) => {
        if (alive) setUnread(count);
      });
    };
    check();
    const timer = window.setInterval(check, POLL_MS);
    // Catch up immediately when the tab comes back, rather than waiting out
    // the remainder of an interval that did not run while it was hidden.
    const onVisible = () => {
      if (document.visibilityState === "visible") check();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  return (
    <NavLink
      to="/inbox"
      className="nav-link notification-bell"
      title={unread ? `${unread} unread` : "Inbox"}
    >
      <span aria-hidden="true">🔔</span>
      <span className="sr-only">Inbox</span>
      {unread > 0 && (
        <span className="notification-badge" aria-label={`${unread} unread`}>
          {unread > 99 ? "99+" : unread}
        </span>
      )}
    </NavLink>
  );
}
