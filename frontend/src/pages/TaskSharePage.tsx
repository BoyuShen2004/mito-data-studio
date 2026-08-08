import { useMemo } from "react";
import { useParams } from "react-router-dom";

import {
  getPublicTaskShareMeta,
  publicTaskShareApi,
  type PublicTaskShareMeta,
} from "../api/viewer";
import AnnotationCanvas from "../features/viewer/AnnotationCanvas";
import ShareViewerActions, {
  useShareAxisControls,
} from "../features/viewer/ShareViewerActions";
import ViewerShell, { ViewerShellMessage } from "../components/ViewerShell";
import { useAsync } from "../hooks/useAsync";

export default function TaskSharePage() {
  const { token = "" } = useParams();
  const meta = useAsync<PublicTaskShareMeta>(
    () => getPublicTaskShareMeta(token),
    [token],
  );
  const readApi = useMemo(() => publicTaskShareApi(token), [token]);
  const { controls: axisControls, onAxisControls } = useShareAxisControls();
  if (meta.loading) {
    return <ViewerShellMessage standalone>Loading shared task…</ViewerShellMessage>;
  }
  if (meta.error) {
    return (
      <ViewerShellMessage standalone tone="error">
        This task share link is invalid.
      </ViewerShellMessage>
    );
  }
  if (!meta.data) return null;
  return (
    <ViewerShell
      standalone
      topbar={
        <div className="share-public-banner">
          <h1>Shared task</h1>
          <span className="muted">
            {meta.data.project_title} · {meta.data.volume_name}
          </span>
          <span className="spacer" />
          <ShareViewerActions controls={axisControls} id="topbar-task-share-axis" />
          <span className="share-public-badge">READ-ONLY · NO ACCOUNT NEEDED</span>
        </div>
      }
    >
      <AnnotationCanvas
        taskId={meta.data.task_id}
        volumeId={meta.data.volume_id}
        zStart={meta.data.z_start}
        zEnd={meta.data.z_end}
        editable={false}
        api={readApi}
        onAxisControls={onAxisControls}
      />
    </ViewerShell>
  );
}
