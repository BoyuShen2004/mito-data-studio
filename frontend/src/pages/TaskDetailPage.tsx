import { Link, Navigate, useParams } from "react-router-dom";
import { getTask } from "../api/tasks";
import { TaskDetailsStack } from "../components/TaskDetailsCards";
import StatusBadge from "../components/StatusBadge";
import { useAuth } from "../auth/AuthContext";
import { useAsync } from "../hooks/useAsync";
import { StreamingStatusCard } from "./VolumeDetailPage";

/** Annotator entry point; managers use the volume route with the same cards. */
export default function TaskDetailPage() {
  const {id} = useParams();
  const taskId = Number(id);
  const {user, isManager} = useAuth();
  const {data: task, loading, error} = useAsync(() => getTask(taskId), [taskId]);

  if (loading) return <p className="muted">Loading…</p>;
  if (error) return <div className="error">{error}</div>;
  if (!task) return null;
  if (isManager) return <Navigate to={`/volumes/${task.volume}`} replace/>;

  const mine = task.assigned_to === user?.id;
  const volume = {
    ...task,
    name: task.volume_name,
    status: task.volume_status,
  };

  return <>
    <div className="row spread">
      <h1>Task #{task.id}</h1>
      <div className="row">
        <StatusBadge value={task.status}/>
        {task.annotation_locked && <span className="muted">🔒 closed</span>}
      </div>
    </div>

    <TaskDetailsStack
      volume={volume}
      tasks={[task]}
      primaryTask={task}
      streamingCard={<StreamingStatusCard
        volume={task}
        isManager={false}
        busy={null}
        notice={null}
        onBuild={() => undefined}
      />}
      taskActions={() => <>
        <Link to={`/viewer/tasks/${task.id}`}><button className="secondary">View</button></Link>
        {mine && task.can_annotate && <Link to={`/editor/tasks/${task.id}`}><button>Annotate</button></Link>}
      </>}
    />
  </>;
}
