export type NotificationVerb =
  | "task.assigned"
  | "task.withdrawn"
  | "submission.received"
  | "submission.reviewed"
  | "hard_case.opened"
  | "hard_case.replied"
  | "deadline.approaching"
  | "milestone.at_risk"
  | "quality.flagged";

export interface AppNotification {
  id: number;
  verb: NotificationVerb;
  title: string;
  body: string;
  /** An SPA path. Stored server-side so an old row survives a route change. */
  url: string;
  actor: string | null;
  target_type: string;
  target_id: string;
  read: boolean;
  created_at: string;
}

export interface NotificationPage {
  results: AppNotification[];
  unread: number;
  limit: number;
  offset: number;
}
