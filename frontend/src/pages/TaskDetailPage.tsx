import { useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { getTask } from "../api/tasks";
import { listSubmissions } from "../api/submissions";
import { listHardCases } from "../api/hardCases";
import { useAuth } from "../auth/AuthContext";
import { useAsync } from "../hooks/useAsync";
import { pendingSubmission, taskTimeline, type TimelineEvent } from "../features/worklist/timeline";
import { displayTaskLayerRange } from "../features/viewer/layerIndex";
import { difficultyLabel, priorityLabel, submissionChannelLabel } from "../labels";
import { relativeTime } from "../time";
import Breadcrumb from "../components/Breadcrumb";
import ReviewBox from "../components/ReviewBox";
import SectionTabs from "../components/SectionTabs";
import StatusBadge from "../components/StatusBadge";
import { AnnotationTimeCell } from "../components/VolumeMeta";
import type { AnnotationTask } from "../types/task";

type TaskTab = "conversation" | "labels" | "history";

/**
 * A task is this application's pull request, and this is its page.
 *
 * The same URL opens the same page for a manager and an annotator — the old
 * `if (isManager) return <Navigate to={/volumes/:id}/>` meant a pasted
 * `/tasks/42` landed two people on two different pages. What differs by role
 * is the action box at the end of the timeline, never the page.
 *
 * The timeline is assembled by `features/worklist/timeline.ts` from fields the
 * task serializer already returns; nothing here fetches history.
 */
export default function TaskDetailPage() {
  const { id } = useParams();
  const taskId = Number(id);
  const { user, isManager } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const task = useAsync(() => getTask(taskId), [taskId]);
  const cases = useAsync(
    () => (task.data ? listHardCases({ volume: task.data.volume }) : Promise.resolve([])),
    [task.data?.volume],
  );

  // Looked up only after a decision, and only for a manager: "what is next"
  // is a question nobody asks until they have finished with this one.
  const [nextHref, setNextHref] = useState<string | null>(null);
  const findNextWaiting = async () => {
    try {
      const waiting = await listSubmissions("submitted");
      const next = waiting.find((row) => row.task !== taskId);
      setNextHref(next ? `/tasks/${next.task}` : null);
    } catch {
      // A queue we could not read is not an error worth interrupting a
      // completed review for — the reviewer simply gets no "next" link.
      setNextHref(null);
    }
  };

  const requested = searchParams.get("tab") as TaskTab | null;
  const active: TaskTab =
    requested === "labels" || requested === "history" ? requested : "conversation";
  const selectTab = (tab: TaskTab) => setSearchParams(tab === "conversation" ? {} : { tab });

  if (task.loading) return <p className="muted">Loading…</p>;
  if (task.error) return <div className="error">{task.error}</div>;
  if (!task.data) return null;

  const t = task.data;
  const range = displayTaskLayerRange(t.z_start, t.z_end);
  const title = `${t.volume_name} z${range}`;
  const events = taskTimeline(t);
  const pending = pendingSubmission(t);
  const mine = t.assigned_to === user?.id;
  const canAnnotate = (isManager || mine) && t.can_annotate;
  const rounds = [...(t.review_history ?? [])].sort((a, b) => b.round_number - a.round_number);

  return (
    <div className="task-page">
      <Breadcrumb
        items={[
          { label: t.project_title, to: `/projects/${t.project}` },
          { label: "Tasks", to: `/projects/${t.project}?tab=tasks` },
          { label: `${title} #${t.id}` },
        ]}
      />

      <header className="task-header row spread">
        <div>
          <div className="row task-title-line">
            <h1>{title}</h1>
            <span className="task-number">#{t.id}</span>
            <StatusBadge value={t.status} />
            {t.annotation_locked && <span className="muted" title="Approved and closed for further annotation.">🔒 closed</span>}
          </div>
          <p className="muted">
            {t.assigned_to_username ? `assigned to ${t.assigned_to_username}` : "unassigned"}
            {" · "}
            <Link to={`/volumes/${t.volume}`}>volume {t.volume_name}</Link>
            {" · "}
            {t.submission_count ?? rounds.length} submission
            {(t.submission_count ?? rounds.length) === 1 ? "" : "s"}
          </p>
        </div>
        {/* View and Annotate are this application's primary verbs; nothing on
            this page outranks them. */}
        <div className="row task-primary-actions">
          <ViewAnnotate task={t} canAnnotate={canAnnotate} />
        </div>
      </header>

      <SectionTabs
        tabs={[
          { id: "conversation", label: "Conversation", count: events.length },
          { id: "labels", label: "Labels" },
          { id: "history", label: "History", count: rounds.length },
        ]}
        active={active}
        onChange={selectTab}
        label="Task sections"
      />

      <div className="workspace-panel" role="tabpanel">
        {active === "conversation" && (
          <div className="task-conversation">
            <div className="task-timeline-column">
              <Timeline events={events} />
              <ActionBox
                task={t}
                isManager={isManager}
                mine={mine}
                pendingSubmissionId={pending?.id ?? null}
                nextHref={nextHref}
                onChanged={() => {
                  task.reload();
                  if (isManager) void findNextWaiting();
                }}
              />
            </div>
            <TaskSidebar task={t} cases={cases.data ?? []} />
          </div>
        )}

        {active === "labels" && (
          <section className="section-block">
            <div className="section-heading">
              <h2>Labels</h2>
              <p className="muted">
                The annotation canvas. View is read-only; Annotate paints the working draft and
                Save writes it — Submit, separately, takes the snapshot a manager reviews.
              </p>
            </div>
            <div className="row task-primary-actions">
              <ViewAnnotate task={t} canAnnotate={canAnnotate} />
              {!canAnnotate && (
                <span className="muted">
                  {t.annotation_locked
                    ? "Annotation is closed on this task."
                    : "This task is assigned to somebody else."}
                </span>
              )}
            </div>
          </section>
        )}

        {active === "history" && <RoundHistory rounds={rounds} />}
      </div>
    </div>
  );
}

/** The pair of primary verbs, defined once so the header and the Labels tab
 * cannot drift into offering different ones. */
function ViewAnnotate({ task, canAnnotate }: { task: AnnotationTask; canAnnotate: boolean }) {
  return (
    <>
      <Link to={`/viewer/tasks/${task.id}`}>
        <button type="button" className="secondary">View</button>
      </Link>
      {canAnnotate && (
        <Link to={`/editor/tasks/${task.id}`}>
          <button type="button">Annotate</button>
        </Link>
      )}
    </>
  );
}

function Timeline({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) {
    return <p className="muted">Nothing has happened on this task yet.</p>;
  }
  return (
    <ol className="task-timeline">
      {events.map((event) => (
        <li key={event.key} className={`timeline-event timeline-event-${event.kind}`}>
          <div className="timeline-line">
            <span className="timeline-title">{event.title}</span>
            {event.badge && <StatusBadge value={event.badge} />}
            <span className="muted timeline-when" title={new Date(event.at).toLocaleString()}>
              {relativeTime(event.at)}
            </span>
          </div>
          {event.body && <p className="timeline-body">{event.body}</p>}
        </li>
      ))}
    </ol>
  );
}

/**
 * What you do next, where the history ends.
 *
 * Role-aware on purpose: whoever cannot act sees the state rather than a
 * disabled button they have to reason about.
 */
function ActionBox({
  task,
  isManager,
  mine,
  pendingSubmissionId,
  nextHref,
  onChanged,
}: {
  task: AnnotationTask;
  isManager: boolean;
  mine: boolean;
  pendingSubmissionId: number | null;
  nextHref: string | null;
  onChanged: () => void;
}) {
  if (isManager && pendingSubmissionId != null) {
    return (
      <ReviewBox
        submissionId={pendingSubmissionId}
        onDecided={onChanged}
        nextHref={nextHref}
      />
    );
  }
  if (isManager) {
    return (
      <div className="review-box review-box-idle">
        <p className="muted" style={{ margin: 0 }}>
          Nothing is waiting on you here. A decision box appears the moment{" "}
          {task.assigned_to_username || "the annotator"} submits.
        </p>
      </div>
    );
  }
  if (!mine) {
    return (
      <div className="review-box review-box-idle">
        <p className="muted" style={{ margin: 0 }}>
          This task is assigned to {task.assigned_to_username || "nobody"}. You can read it and open
          View.
        </p>
      </div>
    );
  }
  return (
    <div className="review-box">
      <h3>Your turn</h3>
      <p className="muted">
        {task.can_annotate
          ? "Paint in Annotate and Save as you go; Submit takes the snapshot a manager reviews."
          : "Annotation is closed on this task."}
      </p>
      <div className="row">
        {task.can_annotate && (
          <Link to={`/editor/tasks/${task.id}`}>
            <button type="button">Annotate</button>
          </Link>
        )}
        {task.can_submit ? (
          <Link to={`/tasks/${task.id}/submit`}>
            <button type="button" className="secondary">Submit a label file (offline)</button>
          </Link>
        ) : (
          <span className="muted">Submitting is closed.</span>
        )}
      </div>
    </div>
  );
}

/** Metadata, so it never competes with the narrative. */
function TaskSidebar({
  task,
  cases,
}: {
  task: AnnotationTask;
  cases: { id: number; app_url: string; status: string }[];
}) {
  const facts: [string, ReactNode][] = [
    ["Assignee", task.assigned_to_username || "—"],
    ["Priority", priorityLabel(task.priority)],
    ["Difficulty", difficultyLabel(task.difficulty)],
    ["Deadline", task.deadline ?? "—"],
    ["Task type", task.task_type.replace(/_/g, " ")],
    ["Frames (z)", displayTaskLayerRange(task.z_start, task.z_end)],
    ["Dataset", task.dataset || "—"],
    ["Label type", <StatusBadge value={task.label_type || "none"} />],
    // `-` means this volume predates time tracking and its real total is
    // unknown — a different statement from `0m`.
    ["Time", <AnnotationTimeCell time={task.annotation_time} />],
    ...(task.instructions
      ? ([["Instructions", <p className="sidebar-instructions">{task.instructions}</p>]] as [string, ReactNode][])
      : []),
    [
      "Cases",
      cases.length === 0 ? (
        <span className="muted">None raised</span>
      ) : (
        <div className="row sidebar-cases">
          {cases.map((row) => (
            <Link key={row.id} to={row.app_url} className={row.status === "open" ? "" : "muted"}>
              #{row.id}
            </Link>
          ))}
        </div>
      ),
    ],
    ["Volume", <Link to={`/volumes/${task.volume}`}>{task.volume_name}</Link>],
  ];

  return (
    <aside className="task-sidebar">
      {facts.map(([label, value]) => (
        <div className="sidebar-field" key={label}>
          <div className="eyebrow">{label}</div>
          <div>{value}</div>
        </div>
      ))}
    </aside>
  );
}

function RoundHistory({ rounds }: { rounds: AnnotationTask["review_history"] }) {
  if (rounds.length === 0) return <p className="muted">No submissions yet.</p>;
  return (
    <section className="section-block">
      <div className="section-heading">
        <h2>Submission rounds</h2>
        <p className="muted">Newest first. Each round is an immutable snapshot.</p>
      </div>
      {rounds.map((round, index) => (
        <details key={round.id} open={index === 0} className="round-details">
          <summary>
            Round {round.round_number} · {submissionChannelLabel(round.source)} ·{" "}
            {new Date(round.submitted_at).toLocaleString()} <StatusBadge value={round.review_status} />
          </summary>
          {round.superseded_reason && <p className="muted">{round.superseded_reason}</p>}
          {round.reviews.length === 0 ? (
            <p className="muted">No decision on this round.</p>
          ) : (
            round.reviews.map((review) => (
              <p key={review.id} className="muted">
                <StatusBadge value={review.decision} /> — {submissionChannelLabel(review.source)} · by{" "}
                {review.reviewer_username || "Unknown reviewer"}
                {review.comments ? ` · ${review.comments}` : " · No comment."}
              </p>
            ))
          )}
        </details>
      ))}
    </section>
  );
}
