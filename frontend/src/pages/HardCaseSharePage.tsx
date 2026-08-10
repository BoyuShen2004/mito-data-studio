import { useMemo } from "react";
import { useParams } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { getPublicHardCaseMeta, publicHardCaseApi } from "../api/viewer";
import ViewerShell, { ViewerShellMessage } from "../components/ViewerShell";
import AnnotationCanvas from "../features/viewer/AnnotationCanvas";
import ShareViewerActions, {
  useShareAxisControls,
} from "../features/viewer/ShareViewerActions";

/**
 * Public, no-account, read-only "hard case" viewer.
 *
 * Reached at `/share/hard-case/:token` — registered OUTSIDE `RequireAuth` and
 * without the app `Layout`/navbar, since a recipient may have no account. It
 * mounts the same `AnnotationCanvas` inside the same `ViewerShell` as the
 * authed viewer (`standalone`, since there's no `Layout fullBleed` above it to
 * supply the viewport height — see `components/ViewerShell.tsx`), but:
 *   - `editable={false}` (no tools / Save / paint / track — view only),
 *   - `api` = the token-backed public endpoints (`publicHardCaseApi`), so no
 *     auth is needed,
 *   - `initialActiveId`/`initialSoloId` = the shared label, so the recipient
 *     lands soloed on it (canvas + 3D). They can reveal other labels from the
 *     Labels panel afterwards — solo is a default, not a lock.
 */
export default function HardCaseSharePage() {
  const { token } = useParams();
  const { data: meta, loading, error } = useAsync(
    () => getPublicHardCaseMeta(token as string),
    [token],
  );
  const api = useMemo(() => publicHardCaseApi(token as string), [token]);
  const { controls: axisControls, onAxisControls } = useShareAxisControls();

  if (loading) {
    return <ViewerShellMessage standalone>Loading shared case…</ViewerShellMessage>;
  }
  if (error || !meta) {
    return (
      <ViewerShellMessage standalone tone="error">
        This share link is invalid, was revoked, or has expired.
      </ViewerShellMessage>
    );
  }

  return (
    <ViewerShell
      standalone
      topbar={
        <div className="share-public-banner">
          <h1>Shared hard case</h1>
          <span className="muted" style={{ fontSize: "0.78rem" }}>
            {meta.project_title} · {meta.volume_name} · label #{meta.label_id}
          </span>
          {meta.note && (
            <span className="hard-case-detail-note" title={meta.note}>
              Note: {meta.note}
            </span>
          )}
          <span className="spacer" />
          <ShareViewerActions controls={axisControls} id="topbar-hard-case-axis" />
          <span className="share-public-badge">READ-ONLY · NO ACCOUNT NEEDED</span>
        </div>
      }
    >
      <AnnotationCanvas
        taskId={meta.task_id}
        volumeId={meta.volume_id}
        zStart={meta.z_start}
        zEnd={meta.z_end}
        editable={false}
        api={api}
        initialActiveId={meta.label_id}
        initialSoloId={meta.label_id}
        onAxisControls={onAxisControls}
      />
    </ViewerShell>
  );
}
