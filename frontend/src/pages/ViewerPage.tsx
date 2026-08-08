import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getTask, listProjectTasks } from "../api/tasks";
import { getVolume } from "../api/volumes";
import { submitInappTask } from "../api/submissions";
import { useAsync } from "../hooks/useAsync";
import { useAuth } from "../auth/AuthContext";
import type { AnnotationTask } from "../types/task";
import ViewerShell, { ViewerShellMessage } from "../components/ViewerShell";
import AnnotationCanvas, {
  type AxisControls,
} from "../features/viewer/AnnotationCanvas";
import AxisSelect from "../features/viewer/AxisSelect";
import SliceViewer from "../features/viewer/SliceViewer";
import ShareControl from "../components/ShareControl";
import type { ViewLocation } from "../features/viewer/viewLocation";
import RegionOnlyButton from "../features/viewer/RegionOnlyButton";

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
  const { data: fetchedTask, loading, error } = useAsync(
    () => getTask(taskId),
    [taskId],
  );
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // The submit response carries the refreshed task, so the topbar updates
  // (status, submission_count, can_submit) *without* re-running the loader —
  // a reload here would unmount the canvas mid-session and throw away the
  // annotator's in-memory slice history.
  const [submittedTask, setSubmittedTask] = useState<AnnotationTask | null>(null);
  const [axisControls, setAxisControls] = useState<AxisControls | null>(null);
  const onAxisControls = useCallback((c: AxisControls | null) => {
    setAxisControls(c);
  }, []);

  if (loading) return <ViewerShellMessage>Loading…</ViewerShellMessage>;
  if (error) return <ViewerShellMessage tone="error">{error}</ViewerShellMessage>;
  const task =
    submittedTask && submittedTask.id === taskId ? submittedTask : fetchedTask;
  if (!task) return null;

  const mayOpenEditor = isManager || task.assigned_to === user?.id;
  const mayPaint = task.can_annotate;
  const locked = task.annotation_locked;

  const submitForReview = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const submission = await submitInappTask(task.id);
      setSubmittedTask(submission.task_detail);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Submit failed");
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
            {submitError && (
              <span className="error editor-topbar-meta">{submitError}</span>
            )}
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
                    <Link to={`/viewer/tasks/${task.id}`}>
                      <button type="button" className="secondary">
                        View only
                      </button>
                    </Link>
                  ) : (
                    <Link to={`/editor/tasks/${task.id}`}>
                      <button type="button" disabled={locked} title={locked ? "Approved and closed for further annotation." : undefined}>
                        Annotate
                      </button>
                    </Link>
                  ))}
              </div>
            </div>
          </div>
        </>
      }
    >
      <AnnotationCanvas
        taskId={task.id}
        volumeId={task.volume}
        zStart={task.z_start}
        zEnd={task.z_end}
        editable={Boolean(editable && mayPaint)}
        onAxisControls={onAxisControls}
      />
    </ViewerShell>
  );
}
