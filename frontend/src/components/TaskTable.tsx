import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import type { AnnotationTask } from "../types/task";
import StatusBadge from "./StatusBadge";
import { DatasetVolumesTable, type VolumeMetaLike } from "./VolumeMeta";

interface Props {
  tasks: AnnotationTask[];
  showAssignee?: boolean;
  showProject?: boolean;
}

type TaskVolumeRow = VolumeMetaLike & AnnotationTask;

/** Task rows use the canonical volume columns; Project/Status/Assignee are
 * optional assignment context, never a parallel Label/Type/Frames schema. */
export default function TaskTable({
  tasks,
  showAssignee = true,
  showProject = false,
}: Props) {
  const { user, isManager } = useAuth();

  if (tasks.length === 0) {
    return <p className="muted">No tasks.</p>;
  }
  const rows: TaskVolumeRow[] = tasks.map((task) => ({
    ...task,
    row_key: task.history_key ?? task.id,
    name: task.volume_name,
  }));
  const hasViewAction = tasks.some((task) => !task.assignment_withdrawn);
  const hasAnnotateAction = tasks.some(
    (task) =>
      !task.assignment_withdrawn &&
      (isManager || task.assigned_to === user?.id) &&
      task.can_annotate,
  );

  const task = (row: VolumeMetaLike) => row as TaskVolumeRow;

  return <DatasetVolumesTable
    volumes={rows}
    tableClassName={showProject && !showAssignee ? "task-table-annotator" : "task-table"}
    actionLabel={hasAnnotateAction ? "View / Annotate" : hasViewAction ? "View" : "Actions"}
    project={showProject ? (row) => {
      const t = task(row);
      return <span title={t.project_title}>{t.project_title}</span>;
    } : undefined}
    status={(row) => {
      const t = task(row);
      return <>
        <StatusBadge value={t.status}/>
        {t.assignment_withdrawn && <div className="muted task-withdrawal-note">
          {t.withdrawal_reason || "Assignment withdrawn"}
        </div>}
        {t.annotation_locked && <span className="muted task-lock" title="Approved and closed for further annotation.">🔒</span>}
      </>;
    }}
    assignee={showAssignee ? (row) => task(row).assigned_to_username || "—" : undefined}
    details={(row) => {
      const t = task(row);
      const detailsTo = isManager ? `/volumes/${t.volume}` : `/tasks/${t.id}`;
      return t.assignment_withdrawn ? <span className="muted">Task #{t.id} {t.assignment_transferred ? "transferred" : "withdrawn"}</span> : <Link to={detailsTo}>Task #{t.id}</Link>;
    }}
    action={(row) => {
      const t = task(row);
      const canEdit = (isManager || t.assigned_to === user?.id) && t.can_annotate;
      if (t.assignment_withdrawn) return <span className="muted">{t.assignment_transferred ? "Transferred" : "Withdrawn"}</span>;
      return <div className="task-actions">
        <Link to={`/viewer/tasks/${t.id}`}><button type="button" className="secondary">View</button></Link>
        {canEdit && <Link to={`/editor/tasks/${t.id}`}><button type="button">Annotate</button></Link>}
      </div>;
    }}
  />;
}
