import { useState } from "react";
import { displayTaskLayerRange } from "../features/viewer/layerIndex";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getSubmission, reviewSubmission } from "../api/submissions";
import { useAsync } from "../hooks/useAsync";
import StatusBadge from "../components/StatusBadge";
import type { ReviewDecision } from "../types";
import { submissionChannelLabel } from "../components/TaskDetailsCards";

export default function ReviewSubmissionPage() {
  const { id } = useParams();
  const submissionId = Number(id);
  const navigate = useNavigate();
  const sub = useAsync(() => getSubmission(submissionId), [submissionId]);

  const [comments, setComments] = useState("");
  // Approve-only. Default off: approving means "done" unless the manager
  // explicitly leaves the task open for another round (see
  // backend/annotation/services.py approve_submission).
  const [allowFurther, setAllowFurther] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const decide = async (decision: ReviewDecision) => {
    setBusy(true);
    setError(null);
    try {
      await reviewSubmission(submissionId, decision, comments, allowFurther);
      navigate("/manager");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Review failed");
    } finally {
      setBusy(false);
    }
  };

  if (sub.loading) return <p className="muted">Loading…</p>;
  if (sub.error) return <div className="error">{sub.error}</div>;
  if (!sub.data) return null;
  const s = sub.data;
  const report = s.qc_report as {
    file_size?: number;
    extension?: string;
    errors?: string[];
    warnings?: string[];
  };

  return (
    <>
      <h1>Review submission #{s.id}</h1>
      <div className="card">
        <table>
          <tbody>
            <tr>
              <th>Task</th>
              <td>
                #{s.task_detail.id} · {s.task_detail.volume_name} · z
                {displayTaskLayerRange(s.task_detail.z_start, s.task_detail.z_end)}
              </td>
            </tr>
            <tr>
              <th>Task status</th>
              <td>
                <StatusBadge value={s.task_detail.status} />
              </td>
            </tr>
            <tr>
              <th>Annotator</th>
              <td>{s.annotator_username}</td>
            </tr>
            <tr>
              <th>Source</th>
              <td>
                {s.source === "inapp" ? (
                  <>
                    {submissionChannelLabel(s.source)} — inspect the submitted
                    snapshot before deciding:{" "}
                    <Link to={`/editor/tasks/${s.task}`}>
                      <button className="secondary">Annotate</button>
                    </Link>
                  </>
                ) : (
                  submissionChannelLabel(s.source)
                )}
              </td>
            </tr>
            {s.source !== "inapp" && (
              <tr>
                <th>Label file</th>
                <td>{s.label_file || "—"}</td>
              </tr>
            )}
            <tr>
              <th>Round</th>
              <td>
                {submissionChannelLabel(s.source)} · channel round {s.round_number}
                {s.task_detail.last_decision ? (
                  <>
                    {" · last decision "}
                    <StatusBadge value={s.task_detail.last_decision} />
                    {s.task_detail.last_decision_by_username
                      ? ` by ${s.task_detail.last_decision_by_username}`
                      : ""}
                  </>
                ) : (
                  " · not reviewed before"
                )}
              </td>
            </tr>
            <tr>
              <th>Notes</th>
              <td>{s.notes || "—"}</td>
            </tr>
            <tr>
              <th>QC</th>
              <td>
                <StatusBadge value={s.qc_status} /> · {report.file_size ?? 0} bytes
                · {report.extension || "?"}
              </td>
            </tr>
          </tbody>
        </table>
        {Boolean(report.errors?.length || report.warnings?.length) && (
          <ul className="muted">
            {report.errors?.map((m, i) => (
              <li key={`e${i}`}>⚠ {m}</li>
            ))}
            {report.warnings?.map((m, i) => (
              <li key={`w${i}`}>• {m}</li>
            ))}
          </ul>
        )}
      </div>

      {s.reviews.length > 0 && (
        <div className="card">
          <h3>Previous reviews</h3>
          {s.reviews.map((r) => (
            <p key={r.id}>
              <StatusBadge value={r.decision} /> — {submissionChannelLabel(r.source)} · by {r.reviewer_username} —{" "}
              {r.comments || "no comment"}
            </p>
          ))}
        </div>
      )}

      <div className="card">
        <h3>Decision</h3>
        {error && <div className="error">{error}</div>}
        <label className="field">
          <span>Comments</span>
          <textarea
            rows={3}
            value={comments}
            onChange={(e) => setComments(e.target.value)}
          />
        </label>
        <label className="row" style={{ gap: "0.5rem", alignItems: "center" }}>
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
          </span>
        </label>
        <div className="row">
          <button onClick={() => decide("approved")} disabled={busy}>
            {allowFurther ? "Approve & keep open" : "Approve & close"}
          </button>
          <button
            className="secondary"
            onClick={() => decide("revision_requested")}
            disabled={busy}
          >
            Request revision
          </button>
          <button
            className="danger"
            onClick={() => decide("rejected")}
            disabled={busy}
          >
            Reject
          </button>
        </div>
        <p className="muted" style={{ marginBottom: 0 }}>
          Reject and Request revision both hand the task back: the annotator
          keeps proofreading and can submit again. Nothing is merged into the
          official mask unless you approve.
        </p>
      </div>
    </>
  );
}
