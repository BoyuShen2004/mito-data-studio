import { useState } from "react";
import { Link } from "react-router-dom";
import { deleteReviewLabelComment, listReviewLabelComments } from "../api/reviewLabelComments";
import { getSubmission, reviewSubmission } from "../api/submissions";
import { useAsync } from "../hooks/useAsync";
import { submissionChannelLabel } from "../labels";
import type { ReviewDecision } from "../types";
import type { ReviewLabelComment } from "../types/reviewLabelComment";
import ReviewLabelCommentList from "./ReviewLabelCommentList";
import StatusBadge from "./StatusBadge";

/**
 * The manager's decision, at the end of the conversation it concludes.
 *
 * This is the whole of the old `pages/ReviewSubmissionPage.tsx`, minus its
 * page chrome and minus the `navigate("/manager")` that used to throw a
 * reviewer back to a dashboard root after every decision. Deciding here leaves
 * you on the task, with the new state and the new timeline entry visible.
 *
 * The review gate itself is untouched: only a manager sees this box, and the
 * server still decides whether the call is allowed.
 */
export default function ReviewBox({
  submissionId,
  onDecided,
  nextHref,
}: {
  submissionId: number;
  /** Refresh the task so the state pill and the timeline show the decision. */
  onDecided: () => void;
  /** Where "Next waiting submission" goes, when the reviewer came from a queue. */
  nextHref?: string | null;
}) {
  const submission = useAsync(() => getSubmission(submissionId), [submissionId]);
  const labelComments = useAsync(() => listReviewLabelComments(submissionId), [submissionId]);

  const [comments, setComments] = useState("");
  // Approve-only. Default off: approving means "done" unless the manager
  // explicitly leaves the task open for another round (see
  // backend/annotation/services.py approve_submission).
  const [allowFurther, setAllowFurther] = useState(false);
  const [busy, setBusy] = useState(false);
  const [decided, setDecided] = useState<ReviewDecision | null>(null);

  const decide = async (decision: ReviewDecision) => {
    setBusy(true);
    try {
      await reviewSubmission(submissionId, decision, comments, allowFurther);
      setDecided(decision);
      setComments("");
      onDecided();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Review failed");
    } finally {
      setBusy(false);
    }
  };

  const removeLabelComment = async (comment: ReviewLabelComment) => {
    try {
      await deleteReviewLabelComment(comment.id);
      labelComments.reload();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Could not delete label comment");
    }
  };

  if (decided) {
    return (
      <div className="review-box review-box-done" id="review">
        <div className="row spread">
          <span>
            <StatusBadge value={decided} /> recorded. The timeline above has it.
          </span>
          {nextHref && <Link to={nextHref}>Next waiting submission →</Link>}
        </div>
      </div>
    );
  }

  if (submission.loading) return <div className="review-box muted" id="review">Loading the submission…</div>;
  if (submission.error) return <div className="error" id="review">{submission.error}</div>;
  if (!submission.data) return null;

  const s = submission.data;
  const report = s.qc_report as {
    file_size?: number;
    extension?: string;
    errors?: string[];
    warnings?: string[];
  };

  return (
    <div className="review-box" id="review">
      <h3>Review this submission</h3>
      <p className="muted">
        Round {s.round_number} · {submissionChannelLabel(s.source)} · by {s.annotator_username}
        {s.source !== "inapp" && s.label_file ? ` · ${s.label_file}` : ""}
      </p>
      <p className="muted">
        QC <StatusBadge value={s.qc_status} /> · {report.file_size ?? 0} bytes · {report.extension || "?"}
        {s.notes ? ` · notes: ${s.notes}` : ""}
      </p>
      {Boolean(report.errors?.length || report.warnings?.length) && (
        <ul className="muted">
          {report.errors?.map((m, i) => <li key={`e${i}`}>⚠ {m}</li>)}
          {report.warnings?.map((m, i) => <li key={`w${i}`}>• {m}</li>)}
        </ul>
      )}

      {/* Submission-aware on purpose: `?submission=` is what makes the viewer
          show *this* snapshot, and it is the only route from which a manager's
          label comment is saved against the round being decided. The task
          header's plain View shows the current working state instead — the two
          are different questions and carry different labels. */}
      {s.source === "inapp" && (
        <div className="row task-actions review-submission-actions">
          <Link to={`/viewer/tasks/${s.task}?submission=${submissionId}`}>
            <button type="button" className="secondary">View this submission</button>
          </Link>
          <Link to={`/editor/tasks/${s.task}`}>
            <button type="button">Annotate</button>
          </Link>
        </div>
      )}

      <div className="review-box-comments">
        {labelComments.loading ? (
          <p className="muted">Loading commented instances…</p>
        ) : labelComments.error ? (
          <div className="error">{labelComments.error}</div>
        ) : (
          <ReviewLabelCommentList
            title="Commented instances"
            comments={Array.isArray(labelComments.data) ? labelComments.data : []}
            canDelete
            onDelete={removeLabelComment}
          />
        )}
      </div>

      <label className="field">
        <span>Comments</span>
        <textarea rows={3} value={comments} onChange={(e) => setComments(e.target.value)} />
      </label>
      <label className="row" style={{ gap: "0.5rem", alignItems: "flex-start" }}>
        <input
          type="checkbox"
          checked={allowFurther}
          onChange={(e) => setAllowFurther(e.target.checked)}
        />
        <span>
          Allow further annotation after approval
          <span className="muted" style={{ display: "block", fontSize: "0.78rem" }}>
            {allowFurther
              ? "The annotator keeps Annotate + Submit; a new submission starts another review round."
              : "Approving closes the task: no more painting or submitting until you reopen it."}
          </span>
          <span className="muted" style={{ display: "block", fontSize: "0.78rem" }}>
            Approval installs this snapshot as a new official checkpoint and starts a fresh
            per-label verification lifecycle for any later round.
          </span>
        </span>
      </label>
      <div className="row">
        <button type="button" onClick={() => decide("approved")} disabled={busy}>
          {allowFurther ? "Approve & keep open" : "Approve & close"}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => decide("revision_requested")}
          disabled={busy}
        >
          Request revision
        </button>
        <button type="button" className="danger" onClick={() => decide("rejected")} disabled={busy}>
          Reject
        </button>
      </div>
      <p className="muted" style={{ marginBottom: 0 }}>
        Reject and Request revision both hand the task back: the annotator keeps proofreading and
        can submit again. Nothing is merged into the official mask unless you approve.
      </p>
    </div>
  );
}
