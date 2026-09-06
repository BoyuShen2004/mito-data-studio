import { api } from "./client";
import type { NotificationPage } from "../types/notification";

export function fetchNotifications(options: {
  unreadOnly?: boolean;
  limit?: number;
  offset?: number;
} = {}): Promise<NotificationPage> {
  const params = new URLSearchParams();
  if (options.unreadOnly) params.set("unread", "1");
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.offset !== undefined) params.set("offset", String(options.offset));
  const query = params.toString();
  return api.get(`/notifications/${query ? `?${query}` : ""}`);
}

/**
 * The bell polls this, so it stays a bare count.
 *
 * Resolves to 0 rather than rejecting when the feature is disabled (503) or
 * the request fails: a broken badge must never surface an error banner over
 * the whole authenticated app.
 */
export async function fetchUnreadCount(): Promise<number> {
  try {
    const result = await api.get<{ unread: number }>(
      "/notifications/unread-count/",
    );
    return result.unread;
  } catch {
    return 0;
  }
}

/** Omit `ids` to mark everything read. */
export function markNotificationsRead(
  ids?: number[],
): Promise<{ marked: number; unread: number }> {
  return api.post("/notifications/read/", ids ? { ids } : {});
}
