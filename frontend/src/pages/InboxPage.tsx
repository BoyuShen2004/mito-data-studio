import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  fetchNotifications,
  markNotificationsRead,
} from "../api/notifications";
import type { AppNotification } from "../types/notification";

const VERB_LABELS: Record<string, string> = {
  "task.assigned": "Assignment",
  "task.withdrawn": "Assignment",
  "submission.received": "Review",
  "submission.reviewed": "Review",
  "hard_case.opened": "Hard case",
  "hard_case.replied": "Hard case",
  "deadline.approaching": "Deadline",
  "milestone.at_risk": "Milestone",
  "quality.flagged": "Quality",
};

export default function InboxPage() {
  const [rows, setRows] = useState<AppNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [disabled, setDisabled] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchNotifications({ unreadOnly })
      .then((page) => {
        setRows(page.results);
        setUnread(page.unread);
        setDisabled(false);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 503) setDisabled(true);
        else setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  }, [unreadOnly]);

  useEffect(load, [load]);

  const markAll = async () => {
    await markNotificationsRead();
    load();
  };

  const openOne = async (row: AppNotification) => {
    if (row.read) return;
    await markNotificationsRead([row.id]);
    setRows((previous) =>
      previous.map((item) =>
        item.id === row.id ? { ...item, read: true } : item,
      ),
    );
    setUnread((count) => Math.max(0, count - 1));
  };

  if (disabled) {
    return (
      <div className="role-home">
        <header className="page-header">
          <h1>Inbox</h1>
        </header>
        <div className="empty-state">
          Notifications are not enabled on this deployment.
        </div>
      </div>
    );
  }

  return (
    <div className="role-home">
      <header className="page-header row spread">
        <div>
          <h1>Inbox</h1>
          <p className="muted">
            Assignments, review decisions, and anything flagged for you.
          </p>
        </div>
        <div className="row page-actions">
          <button
            type="button"
            className="secondary"
            onClick={() => setUnreadOnly((value) => !value)}
          >
            {unreadOnly ? "Show all" : "Unread only"}
          </button>
          <button type="button" onClick={markAll} disabled={unread === 0}>
            Mark all read
          </button>
        </div>
      </header>

      {error && <div className="error">{error}</div>}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : rows.length === 0 ? (
        <div className="empty-state">
          {unreadOnly ? "Nothing unread." : "Nothing here yet."}
        </div>
      ) : (
        <ul className="notification-list">
          {rows.map((row) => (
            <li
              key={row.id}
              className={`notification-row${row.read ? "" : " notification-row-unread"}`}
            >
              <span className="notification-kind">
                {VERB_LABELS[row.verb] ?? "Update"}
              </span>
              <div className="notification-main">
                {row.url ? (
                  <Link to={row.url} onClick={() => void openOne(row)}>
                    {row.title}
                  </Link>
                ) : (
                  <span>{row.title}</span>
                )}
                {row.body && <p className="muted">{row.body}</p>}
              </div>
              <time className="muted" dateTime={row.created_at}>
                {new Date(row.created_at).toLocaleString()}
              </time>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
