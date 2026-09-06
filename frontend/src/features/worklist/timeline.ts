import type { AnnotationTask } from "../../types/task";

/**
 * One task's history as a single chronological list.
 *
 * The task page is a timeline, and a timeline cannot be assembled from three
 * differently-shaped arrays at render time without the reconciliation leaking
 * into JSX. This is that reconciliation, as a pure function: `AnnotationTask`
 * in, sorted `TimelineEvent[]` out.
 *
 * **No new endpoint and no new field.** `AnnotationTaskSerializer` already
 * returns every timestamp below (`assigned_at`, `review_history[].submitted_at`
 * / `.superseded_at` / `.reviews[].reviewed_at`, `approved_at`), so this costs
 * one pass over data the page has already fetched.
 */

export type TimelineEventKind =
  | "created"
  | "assigned"
  | "submitted"
  | "review"
  | "superseded"
  | "approved";

export interface TimelineEvent {
  /** Stable across re-renders — used as the React key. */
  key: string;
  kind: TimelineEventKind;
  /** ISO timestamp. Never null: an event with no time is not emitted. */
  at: string;
  /** Who did it; empty when the server did not record a person. */
  actor: string;
  /** The headline, already phrased. */
  title: string;
  /** What the actor wrote, when they wrote anything. */
  body: string;
  /** A status value for `StatusBadge`, when the event carries a decision. */
  badge?: string;
  /** The submission round this belongs to, when it belongs to one. */
  round?: number;
  /** The submission id this belongs to — the review box needs it. */
  submission?: number;
}

const CHANNEL: Record<string, string> = {
  inapp: "in-app",
  upload: "file upload",
};

const channel = (source: string) => CHANNEL[source] ?? "unknown channel";

const DECISION_VERB: Record<string, string> = {
  approved: "approved this submission",
  rejected: "rejected this submission",
  revision_requested: "requested changes",
};

/** Events at the same instant keep a sensible reading order: you are assigned
 * before you submit, and you submit before anybody reviews it. */
const RANK: Record<TimelineEventKind, number> = {
  created: 0,
  assigned: 1,
  submitted: 2,
  review: 3,
  superseded: 4,
  approved: 5,
};

const time = (value: string) => {
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? null : parsed;
};

export function taskTimeline(task: AnnotationTask): TimelineEvent[] {
  const events: TimelineEvent[] = [];
  const push = (event: Omit<TimelineEvent, "at"> & { at: string | null | undefined }) => {
    if (!event.at || time(event.at) === null) return;
    events.push({ ...event, at: event.at });
  };

  push({
    key: `created-${task.id}`,
    kind: "created",
    at: task.created_at,
    actor: "",
    title: "Task opened",
    body: "",
  });

  push({
    key: `assigned-${task.id}`,
    kind: "assigned",
    at: task.assigned_at,
    actor: task.assigned_to_username,
    title: task.assigned_to_username
      ? `${task.assigned_to_username} was assigned`
      : "Assigned",
    body: "",
  });

  let sawApproval = false;

  for (const round of task.review_history ?? []) {
    push({
      key: `submitted-${round.id}`,
      kind: "submitted",
      at: round.submitted_at,
      actor: round.annotator_username,
      title: `${round.annotator_username || "Somebody"} submitted round ${round.round_number} (${channel(round.source)})`,
      body: "",
      // Deliberately no badge: a submission has no decision of its own. The
      // round's `review_status` mirrors whatever review followed, and showing
      // it here reads as the annotator having approved their own work.
      round: round.round_number,
      submission: round.id,
    });

    for (const review of round.reviews ?? []) {
      if (review.decision === "approved") sawApproval = true;
      push({
        key: `review-${review.id}`,
        kind: "review",
        at: review.reviewed_at,
        actor: review.reviewer_username,
        title: `${review.reviewer_username || "A reviewer"} ${
          DECISION_VERB[review.decision] ?? `recorded ${review.decision.replace(/_/g, " ")}`
        }`,
        body: review.comments,
        badge: review.decision,
        round: round.round_number,
        submission: round.id,
      });
    }

    push({
      key: `superseded-${round.id}`,
      kind: "superseded",
      at: round.superseded_at,
      actor: "",
      title: `Round ${round.round_number} was superseded`,
      body: round.superseded_reason,
      round: round.round_number,
      submission: round.id,
    });
  }

  // Only when no review record already says it. An approval that came through
  // a review is one event, not two — the timeline is the record, and a
  // duplicated entry reads as a second decision that never happened.
  if (!sawApproval) {
    push({
      key: `approved-${task.id}`,
      kind: "approved",
      at: task.approved_at,
      actor: task.last_decision_by_username,
      title: task.last_decision_by_username
        ? `${task.last_decision_by_username} approved this task`
        : "Approved",
      body: task.last_decision_comments ?? "",
      badge: "approved",
    });
  }

  return events.sort((a, b) => {
    const delta = (time(a.at) ?? 0) - (time(b.at) ?? 0);
    return delta !== 0 ? delta : RANK[a.kind] - RANK[b.kind];
  });
}

/** The submission a manager would be deciding on: the newest round that has
 * not been superseded and carries no decision yet. `null` when there is
 * nothing waiting — which is how the action box knows to show state instead of
 * a form. */
export function pendingSubmission(task: AnnotationTask) {
  const rounds = (task.review_history ?? []).filter(
    (round) => !round.superseded_at && round.review_status === "pending",
  );
  if (rounds.length === 0) return null;
  return rounds.reduce((latest, round) =>
    (time(round.submitted_at) ?? 0) >= (time(latest.submitted_at) ?? 0) ? round : latest,
  );
}
