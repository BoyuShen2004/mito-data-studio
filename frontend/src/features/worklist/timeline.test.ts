import { describe, expect, it } from "vitest";
import { pendingSubmission, taskTimeline } from "./timeline";
import type { AnnotationTask } from "../../types/task";

/** A task that has been round-tripped once: submitted, sent back, resubmitted.
 * Every field here is one `AnnotationTaskSerializer` already returns. */
const task = (over: Partial<AnnotationTask> = {}): AnnotationTask =>
  ({
    id: 42,
    assigned_to_username: "alice",
    created_at: "2026-09-03T08:00:00Z",
    assigned_at: "2026-09-03T09:00:00Z",
    submitted_at: "2026-09-05T10:00:00Z",
    approved_at: null,
    last_decision: "revision_requested",
    last_decision_at: "2026-09-04T15:00:00Z",
    last_decision_by_username: "mgr",
    last_decision_comments: "left edge is under-segmented",
    review_history: [
      {
        id: 100,
        round_number: 1,
        annotator_username: "alice",
        submitted_at: "2026-09-04T11:00:00Z",
        superseded_at: "2026-09-05T10:00:00Z",
        superseded_reason: "replaced by round 2",
        source: "inapp",
        review_status: "revision_requested",
        reviews: [
          {
            id: 200,
            decision: "revision_requested",
            source: "inapp",
            comments: "left edge is under-segmented",
            reviewer_username: "mgr",
            reviewed_at: "2026-09-04T15:00:00Z",
          },
        ],
      },
      {
        id: 101,
        round_number: 2,
        annotator_username: "alice",
        submitted_at: "2026-09-05T10:00:00Z",
        superseded_at: null,
        superseded_reason: "",
        source: "inapp",
        review_status: "pending",
        reviews: [],
      },
    ],
    ...over,
  }) as AnnotationTask;

describe("taskTimeline", () => {
  it("interleaves assignment, submissions and reviews in one chronological list", () => {
    expect(taskTimeline(task()).map((event) => [event.kind, event.title])).toEqual([
      ["created", "Task opened"],
      ["assigned", "alice was assigned"],
      ["submitted", "alice submitted round 1 (in-app)"],
      ["review", "mgr requested changes"],
      ["submitted", "alice submitted round 2 (in-app)"],
      ["superseded", "Round 1 was superseded"],
    ]);
  });

  it("carries the reviewer's comment as the event body", () => {
    const review = taskTimeline(task()).find((event) => event.kind === "review");
    expect(review?.body).toBe("left edge is under-segmented");
    expect(review?.badge).toBe("revision_requested");
    expect(review?.round).toBe(1);
  });

  it("names the channel a round came through", () => {
    const events = taskTimeline(
      task({
        review_history: [
          {
            id: 100,
            round_number: 1,
            annotator_username: "alice",
            submitted_at: "2026-09-04T11:00:00Z",
            superseded_at: null,
            superseded_reason: "",
            source: "upload",
            review_status: "pending",
            reviews: [],
          },
        ],
      } as Partial<AnnotationTask>),
    );
    expect(events.map((event) => event.title)).toContain("alice submitted round 1 (file upload)");
  });

  it("does not report an approval twice when a review record already says it", () => {
    const approved = task({
      approved_at: "2026-09-06T09:00:00Z",
      last_decision: "approved",
      last_decision_at: "2026-09-06T09:00:00Z",
      review_history: [
        {
          id: 101,
          round_number: 2,
          annotator_username: "alice",
          submitted_at: "2026-09-05T10:00:00Z",
          superseded_at: null,
          superseded_reason: "",
          source: "inapp",
          review_status: "approved",
          reviews: [
            {
              id: 201,
              decision: "approved",
              source: "inapp",
              comments: "",
              reviewer_username: "mgr",
              reviewed_at: "2026-09-06T09:00:00Z",
            },
          ],
        },
      ],
    } as Partial<AnnotationTask>);
    const approvals = taskTimeline(approved).filter(
      (event) => event.kind === "approved" || event.badge === "approved",
    );
    expect(approvals).toHaveLength(1);
    expect(approvals[0].title).toBe("mgr approved this submission");
  });

  it("still reports an approval that produced no review record", () => {
    const approved = task({
      approved_at: "2026-09-06T09:00:00Z",
      last_decision: "approved",
      last_decision_by_username: "mgr",
      review_history: [],
    } as Partial<AnnotationTask>);
    expect(taskTimeline(approved).map((event) => event.kind)).toContain("approved");
  });

  it("skips an event the server left null rather than dating it now", () => {
    const unassigned = task({ assigned_at: null, review_history: [] } as Partial<AnnotationTask>);
    expect(taskTimeline(unassigned).map((event) => event.kind)).toEqual(["created"]);
  });
});

describe("pendingSubmission", () => {
  it("is the newest round that is neither superseded nor decided", () => {
    expect(pendingSubmission(task())?.id).toBe(101);
  });

  it("is null once every round has been decided", () => {
    const decided = task({
      review_history: task().review_history.map((round) => ({
        ...round,
        superseded_at: null,
        review_status: "approved" as const,
      })),
    });
    expect(pendingSubmission(decided)).toBeNull();
  });

  it("is null for a task nobody has submitted", () => {
    expect(pendingSubmission(task({ review_history: [] } as Partial<AnnotationTask>))).toBeNull();
  });
});
