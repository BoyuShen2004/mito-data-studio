import { useCallback, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getTask, listProjectTasks } from "../api/tasks";
import { getVolume } from "../api/volumes";
import { submitInappTask } from "../api/submissions";
import { useAsync } from "../hooks/useAsync";
import { useAuth } from "../auth/AuthContext";
import type { AnnotationTask } from "../types/task";
import ViewerShell, { ViewerShellMessage } from "../components/ViewerShell";
import { useAnnotationTimer } from "../features/viewer/useAnnotationTimer";
import AnnotationCanvas, {
  type AxisControls,
} from "../features/viewer/AnnotationCanvas";
import AxisSelect from "../features/viewer/AxisSelect";
import SliceViewer from "../features/viewer/SliceViewer";
import ShareControl from "../components/ShareControl";
import { withViewLocation, type ViewLocation } from "../features/viewer/viewLocation";
import RegionOnlyButton from "../features/viewer/RegionOnlyButton";
import {
  listReviewLabelComments,
  saveReviewLabelComment,
} from "../api/reviewLabelComments";
import type { ReviewLabelComment } from "../types/reviewLabelComment";
import { showError } from "../errorPopup";

/**
 * Volume View — same AnnotationCanvas as task View (canvas + Labels + 3D)
 * when the volume has a task; falls back to SliceViewer only if none exist.
 */
export function VolumeViewerPage() {
  const { id } = useParams();
  const volumeId = Number(id);
  const vol = useAsync(() => getVolume(volumeId), [volumeId]);
  const tasks = useAsync(
    () =>
      vol.data
        ? listProjectTasks(vol.data.project).then((all) =>
            all.filter((t) => t.volume === volumeId),
          )
        : Promise.resolve([]),
    [vol.data, volumeId],
  );
  const [axisControls, setAxisControls] = useState<AxisControls | null>(null);
  const [fallbackLocation, setFallbackLocation] = useState<ViewLocation | null>(null);
  const onAxisControls = useCallback((c: AxisControls | null) => {
    setAxisControls(c);
  }, []);

  if (vol.loading || tasks.loading) {
    return <ViewerShellMessage>Loading…</ViewerShellMessage>;
  }
  if (vol.error) {
    return <ViewerShellMessage tone="error">{vol.error}</ViewerShellMessage>;
  }
  if (!vol.data) return null;

  // Prefer a task that covers the full z extent; otherwise the first task.
  const shapeZ = vol.data.shape_z ?? 0;
  const task =
    (tasks.data ?? []).find(
      (t) => t.z_start === 0 && (shapeZ === 0 || t.z_end >= shapeZ),
    ) ?? (tasks.data ?? [])[0];

  if (task) {
    return (
      <ViewerShell
        topbar={
          <>
            <div className="editor-left-slot">
              <h1>View · {vol.data.name}</h1>
              <span className="muted editor-topbar-meta" style={{ fontSize: "0.78rem" }}>
                {vol.data.dataset_name || "Volume"} · Task #{task.id}
              </span>
              <div className="editor-share-slot">
                <ShareControl
                  scope="volume"
                  projectId={vol.data.project}
                  datasetId={vol.data.dataset ?? undefined}
                  volumeId={volumeId}
                  getViewLocation={() => axisControls?.currentLocation() ?? null}
                />
              </div>
            </div>
            <div className="editor-center-slot">
              {axisControls && <RegionOnlyButton controls={axisControls} />}
            </div>
            <div className="editor-right-slot">
              {axisControls && (
                <div className="editor-actions">
                  <AxisSelect
                    id="topbar-view-axis"
                    value={axisControls.axis}
                    onChange={axisControls.changeAxis}
                    disabled={axisControls.disabled}
                  />
                </div>
              )}
            </div>
          </>
        }
      >
        <AnnotationCanvas
          taskId={task.id}
          volumeId={volumeId}
          zStart={task.z_start}
          zEnd={task.z_end}
          mode="view"
          editable={false}
          onAxisControls={onAxisControls}
        />
      </ViewerShell>
    );
  }

  return (
    <ViewerShell topbar={<>
      <h1>View · {vol.data.name}</h1>
      <span className="spacer"/>
      <ShareControl
        scope="volume"
        projectId={vol.data.project}
        datasetId={vol.data.dataset ?? undefined}
        volumeId={volumeId}
        getViewLocation={() => fallbackLocation}
      />
    </>}>
      <SliceViewer volumeId={volumeId} onViewLocation={setFallbackLocation} />
    </ViewerShell>
  );
}

/**
 * Task viewer / editor.
 *
 * Topbar layout — three reserved slots, sized up front (see `.editor-topbar`
 * in styles.css):
 *
 *   [ Annotate|View · Task #N · project · volume · Submit · Share ]
 *   [ Region only · Overwrite ]
 *   [ Axis · View only|Annotate ]
 *
 * Share sits in its own fixed-width sub-slot, wide enough for its expanded
 * "Copy link / Stop sharing" state, so sharing a volume changes what is drawn
 * inside that box and moves nothing else. The centre slot has a width of its
 * own, so it can never paint over the left slot the way the earlier absolutely
 * centred version painted over "Stop sharing".
 *
 * Submit visibility comes from `task.can_submit` (server-decided), never from
 * a status list here — the annotator keeps handing work over after previous
 * submits and rejects, and only an approved-and-locked task loses the button.
 * After a submit we stay on the page (swapping in the refreshed task the
 * submission response carries) rather than bouncing home: the next round
 * starts from right here.
 */
export function TaskViewerPage({ editable = false }: { editable?: boolean }) {
  const { id } = useParams();
  const taskId = Number(id);
  const { user, isManager } = useAuth();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { data: fetchedTask, loading, error } = useAsync(
    () => getTask(taskId),
    [taskId],
  );
  const [submitting, setSubmitting] = useState(false);
  // The submit response carries the refreshed task, so the topbar updates
  // (status, submission_count, can_submit) *without* re-running the loader —
  // a reload here would unmount the canvas mid-session and throw away the
  // annotator's in-memory slice history.
  const [submittedTask, setSubmittedTask] = useState<AnnotationTask | null>(null);
  const [axisControls, setAxisControls] = useState<AxisControls | null>(null);
  const [commentLabelId, setCommentLabelId] = useState<number | null>(null);
  const [commentLocation, setCommentLocation] = useState<ViewLocation | null>(null);
  const reviewSubmissionId = Number(searchParams.get("submission"));
  const canCommentOnReview = Boolean(
    !editable && isManager && Number.isInteger(reviewSubmissionId) && reviewSubmissionId > 0,
  );
  const reviewComments = useAsync(
    () => canCommentOnReview && commentLabelId != null
      ? listReviewLabelComments(reviewSubmissionId)
      : Promise.resolve([] as ReviewLabelComment[]),
    [canCommentOnReview, commentLabelId, reviewSubmissionId],
  );
  const onAxisControls = useCallback((c: AxisControls | null) => {
    setAxisControls(c);
  }, []);

  if (loading) return <ViewerShellMessage>Loading…</ViewerShellMessage>;
  if (error) return <ViewerShellMessage tone="error">{error}</ViewerShellMessage>;
  const task =
    submittedTask && submittedTask.id === taskId ? submittedTask : fetchedTask;
  if (!task) return null;

  const focusedLabel = Number(searchParams.get("label"));
  const shouldFocusFeedback = Boolean(
    searchParams.get("feedback") && Number.isInteger(focusedLabel) && focusedLabel > 0,
  );
  const editingComment = commentLabelId == null
    ? undefined
    : Array.isArray(reviewComments.data)
      ? reviewComments.data.find((row) => row.label_id === commentLabelId)
      : undefined;

  const mayOpenEditor = isManager || task.assigned_to === user?.id;
  const mayPaint = task.can_annotate;
  const locked = task.annotation_locked;
  const switchMode = (path: string) => {
    // Mode is presentation state, so review context must survive alongside
    // the live axis/layer/label location. Dropping feedback/submission here
    // turned a focused review into an ordinary task open.
    const target = new URL(path, window.location.origin);
    searchParams.forEach((value, key) => target.searchParams.set(key, value));
    const withLocation = withViewLocation(
      `${target.pathname}${target.search}`,
      axisControls?.currentLocation(),
    );
    const url = new URL(withLocation, window.location.origin);
    navigate(`${url.pathname}${url.search}`);
  };

  const submitForReview = async () => {
    setSubmitting(true);
    try {
      const submission = await submitInappTask(task.id);
      setSubmittedTask(submission.task_detail);
    } catch (e) {
      showError(e instanceof Error ? e.message : "Submit failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ViewerShell
      topbar={
        <>
          <div className="editor-left-slot">
            <h1>
              {editable ? "Annotate" : "View"} · Task #{task.id}
            </h1>
            <span className="muted editor-topbar-meta" style={{ fontSize: "0.78rem" }}>
              {task.project_title} · {task.volume_name}
            </span>
            {editable && task.can_submit ? (
              <button
                type="button"
                className="secondary editor-submit-btn"
                onClick={submitForReview}
                disabled={submitting}
                title={
                  task.submission_count > 0
                    ? "Hand the current state to a manager again — this replaces your previous submission. Save your layer edits first: unsaved paint is not on disk."
                    : "Hand this task to a manager for review. Save your layer edits first — unsaved paint is not on disk."
                }
              >
                {submitting
                  ? "Submitting…"
                  : task.submission_count > 0
                    ? "Submit again"
                    : "Submit for review"}
              </button>
            ) : editable && locked ? (
              <span
                className="muted editor-lock-note"
                title="A manager approved this task and closed it for further annotation. Ask them to reopen it if you need to keep working."
              >
                🔒 Approved — closed for further annotation
              </span>
            ) : null}
            <div className="editor-share-slot">
              <ShareControl
                scope="volume"
                projectId={task.project}
                volumeId={task.volume}
                getViewLocation={() => axisControls?.currentLocation() ?? null}
              />
            </div>
          </div>
          <div className="editor-center-slot">
            {axisControls && <RegionOnlyButton controls={axisControls} />}
          </div>
          <div className="editor-right-slot">
            <div className="editor-actions">
              {axisControls && (
                <AxisSelect
                  id="topbar-task-axis"
                  value={axisControls.axis}
                  onChange={axisControls.changeAxis}
                  disabled={axisControls.disabled}
                />
              )}
              <div className="editor-mode-slot">
                {mayOpenEditor &&
                  (editable ? (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => switchMode(`/viewer/tasks/${task.id}`)}
                    >
                      View only
                    </button>
                  ) : (
                    <button
                      type="button"
                      disabled={locked}
                      title={locked ? "Approved and closed for further annotation." : undefined}
                      onClick={() => switchMode(`/editor/tasks/${task.id}`)}
                    >
                      Annotate
                    </button>
                  ))}
              </div>
            </div>
          </div>
        </>
      }
    >
      <>
        {/* Automatic annotation timing. Mounted only for the *editable* route
            and only when the server says this person may actually paint, so a
            read-only viewer, a manager looking at someone's task, and the
            Details page never start a clock. The component renders nothing —
            it exists so the hook can run below this page's early returns. */}
        <AnnotationTimer
          taskId={task.id}
          enabled={Boolean(editable && mayPaint && task.assigned_to === user?.id)}
        />
        <AnnotationCanvas
          taskId={task.id}
          volumeId={task.volume}
          zStart={task.z_start}
          zEnd={task.z_end}
          mode={editable ? "annotate" : "view"}
          editable={Boolean(editable && mayPaint)}
          initialActiveId={shouldFocusFeedback ? focusedLabel : undefined}
          initialSoloId={shouldFocusFeedback ? focusedLabel : null}
          onCommentLabel={
            canCommentOnReview
              ? (labelId) => {
                  setCommentLocation(axisControls?.currentLocation() ?? null);
                  setCommentLabelId(labelId);
                }
              : undefined
          }
          onAxisControls={onAxisControls}
        />
        {commentLabelId != null && (
          <ReviewLabelCommentModal
            key={`${reviewSubmissionId}-${commentLabelId}-${editingComment?.id ?? "new"}`}
            submissionId={reviewSubmissionId}
            taskId={task.id}
            labelId={commentLabelId}
            existing={editingComment}
            location={
              commentLocation ??
              ({
                ...({ z: 0, y: 0, x: 0, axis: "z" } as const),
                label: commentLabelId,
              })
            }
            onClose={() => {
              setCommentLabelId(null);
              setCommentLocation(null);
            }}
            onSaved={() => {
              reviewComments.reload();
              setCommentLabelId(null);
              setCommentLocation(null);
            }}
          />
        )}
      </>
    </ViewerShell>
  );
}

function ReviewLabelCommentModal({
  submissionId,
  taskId,
  labelId,
  existing,
  location,
  onClose,
  onSaved,
}: {
  submissionId: number;
  taskId: number;
  labelId: number;
  existing?: ReviewLabelComment;
  location?: ViewLocation | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [body, setBody] = useState(existing?.body ?? "");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    if (!body.trim()) return;
    setBusy(true);
    try {
      await saveReviewLabelComment(
        submissionId,
        taskId,
        labelId,
        body.trim(),
        location ?? null,
      );
      onSaved();
    } catch (e) {
      showError(e instanceof Error ? e.message : "Could not save label comment.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="share-modal-backdrop" onMouseDown={onClose}>
      <div
        className="share-modal review-label-comment-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="review-label-comment-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <h3 id="review-label-comment-title">Comment on label #{labelId}</h3>
        <label className="field">
          <span>Manager feedback</span>
          <textarea
            autoFocus
            rows={4}
            maxLength={1000}
            value={body}
            onChange={(event) => setBody(event.target.value)}
            placeholder="What should the annotator review on this mitochondrion?"
          />
        </label>
        <div className="row spread">
          <span className="muted">{body.length}/1000</span>
          <div className="row">
            <button type="button" className="secondary" onClick={onClose} disabled={busy}>Cancel</button>
            <button type="button" onClick={() => void save()} disabled={busy || !body.trim()}>
              {busy ? "Saving…" : existing ? "Save changes" : "Save comment"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Runs the timing lease for as long as the editable editor is mounted.
 *
 * A component rather than a call in `TaskViewerPage` because that page returns
 * early while the task loads, and a hook cannot live behind a conditional
 * return. Renders nothing: the timer's only visible output is the cumulative
 * duration on the task Details card, which reads it from the server. */
function AnnotationTimer({ taskId, enabled }: { taskId: number; enabled: boolean }) {
  useAnnotationTimer(taskId, enabled);
  return null;
}
