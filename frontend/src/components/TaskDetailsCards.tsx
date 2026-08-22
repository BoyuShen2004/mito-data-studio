import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { displayTaskLayerRange } from "../features/viewer/layerIndex";
import { difficultyLabel, priorityLabel } from "../labels";
import type { AnnotationTask } from "../types/task";
import type { VolumeMetaLike } from "./VolumeMeta";
import { VolumeMetaBlock } from "./VolumeMeta";
import StatusBadge from "./StatusBadge";
import type { AnnotationTimeSummary } from "../api/timing";
import { durationTitle, formatDuration } from "../time";

export const submissionChannelLabel = (source?: string) =>
  source === "inapp" ? "Online (in-app)" : source === "upload" ? "Offline (file upload)" : "Unknown channel";

type DetailVolume = VolumeMetaLike & {
  project: number;
  dataset_name?: string;
  image_location?: string;
  label_location?: string;
  status?: string;
};

const shortLocation = (value?: string) => value ? value.split(/[\\/]/).pop() || value : "—";

export function ReviewCards({ task }: { task: AnnotationTask }) {
  return <>
    {task.last_decision && (
      <section className="card">
        <h3 style={{marginTop: 0}}>Latest review</h3>
        <p style={{marginBottom: 0}}>
          <StatusBadge value={task.last_decision}/>{" — "}
          {submissionChannelLabel(task.last_decision_source)}
          {task.last_decision_by_username ? ` · by ${task.last_decision_by_username}` : ""}
          {task.last_decision_at ? ` · ${new Date(task.last_decision_at).toLocaleString()}` : ""}
        </p>
        <p className="muted" style={{marginBottom: 0}}>{task.last_decision_comments || "No comment."}</p>
      </section>
    )}
    {task.review_history.length > 0 && (
      <section className="card">
        <h3>Review history</h3>
        {task.review_history.map((round) => <div key={round.id} className="review-history-row">
          <strong>{submissionChannelLabel(round.source)} · round {round.round_number}</strong>
          {` · ${new Date(round.submitted_at).toLocaleString()} · `}
          <StatusBadge value={round.review_status}/>
          {round.superseded_reason && <span className="muted"> · {round.superseded_reason}</span>}
          {round.reviews.map((review) => <p key={review.id} className="muted">
            <StatusBadge value={review.decision}/>{" — "}
            {submissionChannelLabel(review.source)} · by {review.reviewer_username || "Unknown reviewer"}
            {review.comments ? ` · ${review.comments}` : " · No comment."}
          </p>)}
        </div>)}
      </section>
    )}
  </>;
}

export function MetadataDetailsCard({volume, task}: {volume: DetailVolume; task: AnnotationTask}) {
  return <section className="card details-metadata-card">
    <h3>Metadata</h3>
    <VolumeMetaBlock volume={volume} scientificMetadata={task.dataset_metadata}/>
    <table><tbody>
      <tr><th>Project</th><td>{task.project_title || `Project #${volume.project}`}</td></tr>
      <tr><th>Dataset</th><td>{volume.dataset_name || task.dataset || "—"}</td></tr>
      <tr><th>Data layers</th><td className="data-layer-lines">
        <div title={volume.image_location || task.image_location}>Raw · {shortLocation(volume.image_location || task.image_location)}</div>
        {volume.has_region_mask && <div title={volume.region_mask_location || task.region_mask_location}>Region · {shortLocation(volume.region_mask_location || task.region_mask_location)}</div>}
        <div title={volume.label_location || task.label_location}>Labels · {shortLocation(volume.label_location || task.label_location)} <StatusBadge value={volume.label_type || task.label_type || "none"}/></div>
      </td></tr>
      <tr><th>Status</th><td>{volume.status || task.volume_status || "—"}</td></tr>
    </tbody></table>
  </section>;
}

export function TaskDetailsCard({task: t, actions}: {task: AnnotationTask; actions?: ReactNode}) {
  return <section className="card details-task-card">
    <div className="row spread">
      <h3 style={{margin: 0}}>Task #{t.id} <StatusBadge value={t.status}/></h3>
      {actions && <div className="row">{actions}</div>}
    </div>
    <table><tbody>
      <tr><th>Assignee</th><td>{t.assigned_to_username || "—"}</td></tr>
      <tr><th>Label type</th><td><StatusBadge value={t.label_type || "none"}/></td></tr>
      <tr><th>Task type</th><td>{t.task_type.replace(/_/g, " ")}</td></tr>
      <tr><th>Frames (z)</th><td>{displayTaskLayerRange(t.z_start, t.z_end)}</td></tr>
      <tr><th>Priority</th><td>{priorityLabel(t.priority)}</td></tr>
      <tr><th>Difficulty</th><td>{difficultyLabel(t.difficulty)}</td></tr>
      <tr><th>Deadline</th><td>{t.deadline ?? "—"}</td></tr>
      <tr><th>Instructions</th><td>{t.instructions || "—"}</td></tr>
      {/* Directly below Instructions, deliberately one plain row: cumulative
          annotation time is useful context, not the point of the page. `-`
          means this volume predates time tracking and its real total is
          unknown — which is a different statement from `0m`. */}
      <tr><th>Time</th><td><AnnotationTimeCell time={t.annotation_time}/></td></tr>
    </tbody></table>
  </section>;
}

/** Cumulative annotation time, with the precise value in a tooltip. */
export function AnnotationTimeCell({time}: {time?: AnnotationTimeSummary}) {
  const tracked = time?.tracked ?? false;
  const seconds = tracked ? time?.seconds ?? 0 : null;
  return <span
    className={`annotation-time${tracked ? "" : " annotation-time-unknown"}`}
    title={durationTitle(seconds, {legacy: !tracked})}
  >
    {time?.display ?? formatDuration(seconds)}
  </span>;
}

/** The one place the Details vertical order is written down.
 *
 * Manager (volume route) and annotator (task route) previously each spelled
 * out the same sequence, and drifted: the annotator merged Metadata into the
 * Task card and the manager had no offline-upload box at all. Roles differ
 * here only by the slots they fill — never by which cards exist or their
 * order. ``metadataCard`` exists so the manager's "Edit metadata" mode can
 * swap **that card alone**; there is deliberately no way to pass task fields
 * into it, which is what keeps Metadata and Task # two separate cards. */
export function TaskDetailsStack({
  volume,
  tasks,
  primaryTask,
  streamingCard,
  metadataCard,
  taskActions,
  emptyMetadata,
}: {
  volume: DetailVolume;
  tasks: AnnotationTask[];
  primaryTask?: AnnotationTask | null;
  streamingCard?: ReactNode;
  metadataCard?: ReactNode;
  taskActions?: (task: AnnotationTask) => ReactNode;
  emptyMetadata?: ReactNode;
}) {
  return <>
    {primaryTask && <ReviewCards task={primaryTask}/>}
    {streamingCard}
    {metadataCard ?? (primaryTask
      ? <MetadataDetailsCard volume={volume} task={primaryTask}/>
      : emptyMetadata)}
    {tasks.map((task) => (
      <TaskDetailsCard key={task.id} task={task} actions={taskActions?.(task)}/>
    ))}
    {primaryTask && <OfflineUploadCard task={primaryTask}/>}
  </>;
}

/** Whether this task already has an *offline* round.
 *
 * Scoped to the upload channel on purpose: `submission_count` counts online
 * submits too, so keying the button off it made the Details card and the
 * upload page disagree after an in-app submit. */
export const hasOfflineRound = (task: AnnotationTask) =>
  task.review_history.some((round) => round.source === "upload");

export const offlineSubmitLabel = (task: AnnotationTask) =>
  hasOfflineRound(task) ? "Submit a new label file" : "Submit completed label";

export function OfflineUploadCard({task}: {task: AnnotationTask}) {
  return <section className="card details-offline-upload-card">
    <div className="row spread">
      <div>
        <h3 style={{margin: 0}}>Offline annotation upload</h3>
        <p className="muted" style={{marginBottom: 0}}>Upload a completed label file as the offline review channel.</p>
      </div>
      {task.can_submit ? <Link to={`/tasks/${task.id}/submit`}>
        <button type="button">{offlineSubmitLabel(task)}</button>
      </Link> : <span className="muted">Annotation is closed.</span>}
    </div>
  </section>;
}
