import { useEffect, useState } from "react";
import {
  createPublicShare,
  getPublicShareTree,
  revokePublicShare,
  type AggregateShareState,
  type PublicShare,
  type PublicShareTree as ShareTreeData,
  type ShareDatasetNode,
  type ShareProjectNode,
  type ShareVolumeNode,
} from "../api/shares";
import { useAsync } from "../hooks/useAsync";
import RegionCoverage from "./RegionCoverage";

function Led({state}: {state: AggregateShareState | "shared" | "not_shared"}) {
  const label = state === "all" ? "All shared" : state === "partial" ? "Partially shared" : state === "shared" ? "Shared" : "Not shared";
  return <span className={`share-led share-led-${state}`} title={label} aria-label={label}/>;
}

function datasetState(dataset: ShareDatasetNode): AggregateShareState {
  if (dataset.direct_shares.length) return "all";
  if (dataset.volumes.length && dataset.volumes.every(volume => volume.shared)) return "all";
  if (dataset.volumes.some(volume => volume.shared)) return "partial";
  return "none";
}

function projectState(project: ShareProjectNode): AggregateShareState {
  if (project.direct_shares.length) return "all";
  const states = [
    ...project.datasets.map(dataset => dataset.state),
    ...project.ungrouped_volumes.map(volume => volume.shared ? "all" : "none"),
  ];
  if (states.length && states.every(state => state === "all")) return "all";
  if (states.some(state => state !== "none")) return "partial";
  return "none";
}

export function patchShareTree(
  tree: ShareTreeData,
  target: {scope: PublicShare["scope"]; project_id: number; dataset_id?: number; volume_id?: number},
  directShares: PublicShare[],
): ShareTreeData {
  return {
    ...tree,
    projects: tree.projects.map(project => {
      if (project.id !== target.project_id) return project;
      let next: ShareProjectNode = {
        ...project,
        direct_shares: target.scope === "project" ? directShares : project.direct_shares,
        datasets: project.datasets.map(dataset => {
          if (target.dataset_id !== dataset.id) return dataset;
          let nextDataset: ShareDatasetNode = {
            ...dataset,
            direct_shares: target.scope === "dataset" ? directShares : dataset.direct_shares,
            volumes: dataset.volumes.map(volume =>
              target.scope === "volume" && target.volume_id === volume.id
                ? {...volume, shared: directShares.length > 0, direct_shares: directShares}
                : volume,
            ),
          };
          nextDataset = {...nextDataset, state: datasetState(nextDataset)};
          return nextDataset;
        }),
        ungrouped_volumes: project.ungrouped_volumes.map(volume =>
          target.scope === "volume" && target.volume_id === volume.id
            ? {...volume, shared: directShares.length > 0, direct_shares: directShares}
            : volume,
        ),
      };
      next = {...next, state: projectState(next)};
      return next;
    }),
  };
}

export default function PublicShareTree() {
  const remote = useAsync(getPublicShareTree, []);
  const [data, setData] = useState<ShareTreeData | null>(null);
  const [openProjects, setOpenProjects] = useState<Set<number>>(new Set());
  const [openDatasets, setOpenDatasets] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [messageIsError, setMessageIsError] = useState(false);

  useEffect(() => {
    if (remote.data) setData(remote.data);
  }, [remote.data]);

  const toggle = (source: Set<number>, set: (next: Set<number>) => void, id: number) => {
    const next = new Set(source);
    next.has(id) ? next.delete(id) : next.add(id);
    set(next);
  };
  const share = async (key: string, params: Parameters<typeof patchShareTree>[1]) => {
    setBusy(key);
    setMessage("");
    setMessageIsError(false);
    try {
      const row = await createPublicShare(params);
      setData(current => current ? patchShareTree(current, params, [row]) : current);
      const url = new URL(row.url, window.location.origin).toString();
      try {
        await navigator.clipboard.writeText(url);
      } catch {
        // Share is on; Copy is available — no success tip.
      }
      setMessage(`${params.scope[0].toUpperCase()}${params.scope.slice(1)} share created.`);
    } catch (e) {
      setMessageIsError(true);
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  };
  const stop = async (key: string, params: Parameters<typeof patchShareTree>[1], rows: PublicShare[]) => {
    setBusy(key);
    setMessage("");
    setMessageIsError(false);
    try {
      await Promise.all(rows.map(row => revokePublicShare(row.id)));
      setData(current => current ? patchShareTree(current, params, []) : current);
      setMessage(`${params.scope[0].toUpperCase()}${params.scope.slice(1)} share stopped.`);
    } catch (e) {
      setMessageIsError(true);
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  };
  const actions = (key: string, params: Parameters<typeof patchShareTree>[1], rows: PublicShare[]) => (
    <span className="row">
      {rows.length ? <>
        <button type="button" className="secondary" disabled={busy === key} onClick={() => stop(key, params, rows)}>Stop</button>
        <button type="button" className="secondary" onClick={() => navigator.clipboard.writeText(new URL(rows[0].url, window.location.origin).toString())}>Copy</button>
      </> : (
        <button type="button" className="secondary" disabled={busy === key} onClick={() => share(key, params)}>Share</button>
      )}
    </span>
  );
  const volumeRow = (volume: ShareVolumeNode, projectId: number, datasetId?: number) => {
    const key = `v${volume.id}`;
    const params = {scope: "volume" as const, project_id: projectId, dataset_id: datasetId, volume_id: volume.id};
    return <div className="share-tree-row share-tree-volume" key={key}>
      <Led state={volume.shared ? "shared" : "not_shared"}/><span>{volume.name}</span>
      <RegionCoverage hasMask={Boolean(volume.has_region_mask)} coverage={volume.region_mask_coverage}/>
      {actions(key, params, volume.direct_shares)}
    </div>;
  };

  if (remote.loading && !data) return <div className="card muted">Loading share hierarchy…</div>;
  return <div className="card">
    <h3>Live public shares</h3>
    <p className="muted">Expand projects and datasets to manage view-only links. Stop revokes only that row’s direct link; child links remain live.</p>
    {(data?.projects ?? []).map(project => {
      const pkey = `p${project.id}`;
      const expanded = openProjects.has(project.id);
      const params = {scope: "project" as const, project_id: project.id};
      return <div className="share-tree-project" key={pkey}>
        <div className="share-tree-row">
          <button className="tree-toggle" type="button" aria-expanded={expanded} onClick={() => toggle(openProjects, setOpenProjects, project.id)}>{expanded ? "▾" : "▸"}</button>
          <Led state={project.state}/><strong>{project.title}</strong>
          {actions(pkey, params, project.direct_shares)}
        </div>
        {expanded && <div className="share-tree-children">
          {project.datasets.map(dataset => {
            const dkey = `d${dataset.id}`;
            const dopen = openDatasets.has(dataset.id);
            const datasetParams = {scope: "dataset" as const, project_id: project.id, dataset_id: dataset.id};
            return <div key={dkey}>
              <div className="share-tree-row">
                <button className="tree-toggle" type="button" aria-expanded={dopen} onClick={() => toggle(openDatasets, setOpenDatasets, dataset.id)}>{dopen ? "▾" : "▸"}</button>
                <Led state={dataset.state}/><span>{dataset.name}</span>
                {actions(dkey, datasetParams, dataset.direct_shares)}
              </div>
              {dopen && <div className="share-tree-children">
                {dataset.volumes.length ? dataset.volumes.map(volume => volumeRow(volume, project.id, dataset.id)) : <p className="muted">No volumes.</p>}
              </div>}
            </div>;
          })}
          {project.ungrouped_volumes.length > 0 && <div>
            <div className="muted">Ungrouped volumes</div>
            {project.ungrouped_volumes.map(volume => volumeRow(volume, project.id))}
          </div>}
        </div>}
      </div>;
    })}
    {!remote.loading && (data?.projects.length ?? 0) === 0 && <p className="muted">No projects yet.</p>}
    {message && <p className={messageIsError ? "error" : "info"} aria-live="polite">{message}</p>}
  </div>;
}
