import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { getPublicShare } from "../api/shares";
import { publicScopedShareApi } from "../api/viewer";
import PublicShareBrowse, { PublicShareChrome, type DatasetKey } from "../components/PublicShareBrowse";
import ViewerShell, { ViewerShellMessage } from "../components/ViewerShell";
import AnnotationCanvas from "../features/viewer/AnnotationCanvas";
import ShareViewerActions, {
  useShareAxisControls,
} from "../features/viewer/ShareViewerActions";
import { useAsync } from "../hooks/useAsync";

/**
 * Anonymous `/share/public/:token` surface. Three browse states on top of one
 * `getPublicShare` payload:
 *
 * - **project** share → dataset index → that dataset's volume table → viewer
 * - **dataset** share → volume table → viewer
 * - **volume** share → straight into the viewer (nothing to browse)
 *
 * The viewer is the same `ViewerShell` + `AnnotationCanvas` the authenticated
 * View route uses, always `editable={false}` — a public link never gets
 * annotate tools (see docs/product-invariants.md on read-only sharing).
 */
export default function PublicSharePage() {
  const {token = ""} = useParams();
  const share = useAsync(() => getPublicShare(token), [token]);
  const [openDataset, setOpenDataset] = useState<DatasetKey | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const data = share.data;
  // A volume-scope share carries exactly the shared volume, so it opens itself.
  const volume = data
    ? data.volumes.find(row => row.id === selected) ?? (data.scope === "volume" ? data.volumes[0] ?? null : null)
    : null;
  const readApi = useMemo(() => volume ? publicScopedShareApi(token, volume.id) : undefined, [token, volume]);
  const {controls: axisControls, onAxisControls} = useShareAxisControls();

  if (share.loading) return <ViewerShellMessage standalone>Loading shared data…</ViewerShellMessage>;
  if (share.error) return <PublicShareChrome>
    <div className="public-share-head"><h1>This link isn’t available</h1></div>
    <div className="empty-state">{share.error}</div>
  </PublicShareChrome>;
  if (!data) return null;

  if (!volume || !readApi) return <PublicShareBrowse
    share={data}
    openDataset={openDataset}
    onOpenDataset={setOpenDataset}
    onOpenVolume={setSelected}
  />;

  const datasetName = data.datasets.find(row => row.id === volume.dataset_id)?.name;
  return <ViewerShell standalone topbar={<div className="share-public-banner">
    {data.scope !== "volume" && <button className="secondary" type="button" onClick={() => setSelected(null)}>← Browse</button>}
    <strong className="public-share-volume-name" title={volume.name}>{volume.name}</strong>
    <span className="muted">{data.project_title}{datasetName ? ` › ${datasetName}` : ""}</span>
    <span className="spacer"/>
    <ShareViewerActions controls={axisControls} id="topbar-share-axis"/>
    <span className="share-public-badge">READ-ONLY · NO ACCOUNT NEEDED</span>
  </div>}>
    <AnnotationCanvas
      taskId={0}
      volumeId={volume.id}
      zStart={0}
      zEnd={Math.max((volume.shape[0] ?? 1) - 1, 0)}
      editable={false}
      api={readApi}
      onAxisControls={onAxisControls}
    />
  </ViewerShell>;
}
