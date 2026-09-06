import { Navigate, useParams } from "react-router-dom";
import { getSubmission } from "../api/submissions";
import { useAsync } from "../hooks/useAsync";

/**
 * `/submissions/:id/review` → `/tasks/:taskId#review`.
 *
 * Reviewing on a page that is not the task's own page was the clearest case of
 * the right function in the wrong place: the reviewer read the history in one
 * route and acted in another, then got redirected to a dashboard root. The
 * form now lives at the end of the task's timeline (`components/ReviewBox.tsx`).
 *
 * This route stays so links people already sent — and the tests that follow
 * them — keep working.
 */
export default function ReviewSubmissionPage() {
  const { id } = useParams();
  const submission = useAsync(() => getSubmission(Number(id)), [id]);

  if (submission.loading) return <p className="muted">Loading…</p>;
  if (submission.error) return <div className="error">{submission.error}</div>;
  if (!submission.data) return null;
  return <Navigate to={`/tasks/${submission.data.task}#review`} replace />;
}
