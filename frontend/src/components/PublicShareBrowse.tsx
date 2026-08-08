import { useState, type ReactNode } from "react";
import type { PublicShareBrowse as PublicShareData } from "../api/shares";
import { DatasetVolumesTable } from "./VolumeMeta";

/**
 * Anonymous browse surface for `/share/public/:token`.
 *
 * The recipient has no account, so `Layout`/`Navbar` are off limits — but the
 * page should still read like the authenticated data browser (project header →
 * dataset list → volume table) rather than a dump of "Open <file>" buttons.
 * These components reuse the app's own chrome (`.navbar`, `.container`,
 * `.section-block`, `.table-wrap`) so the two surfaces stay in visual sync.
 *
 * Read-only by construction: nothing here can mutate, and the only action a row
 * offers is View, which mounts the viewer with `editable={false}`.
 */

export type ShareVolume = PublicShareData["volumes"][number];
/** Which dataset's volumes are open. `"ungrouped"` = volumes with no dataset
 * (registered before dataset grouping existed), same bucket the project page
 * shows under "Ungrouped volumes". */
export type DatasetKey = number | "ungrouped";

const SCOPE_LABEL: Record<PublicShareData["scope"], string> = {
  project: "Project share",
  dataset: "Dataset share",
  volume: "Volume share",
};

/** Volumes shown at a given drill-down level. */
export function volumesIn(share: PublicShareData, dataset: DatasetKey | null): ShareVolume[] {
  if (dataset == null) return share.volumes;
  if (dataset === "ungrouped") return share.volumes.filter(volume => volume.dataset_id == null);
  return share.volumes.filter(volume => volume.dataset_id === dataset);
}

/** Standalone app shell: same bar as `Navbar`, minus every authenticated control. */
export function PublicShareChrome({scope, children}: {scope?: PublicShareData["scope"]; children: ReactNode}) {
  return <div className="public-share-page">
    <header className="navbar public-share-topbar">
      <span className="brand">🧬 Mito Data Agent</span>
      {scope && <span className="muted public-share-scope">{SCOPE_LABEL[scope]}</span>}
      <span className="spacer"/>
      {/* No badge on a dead link — it would promise access the page cannot give. */}
      {scope && <span className="share-public-badge">READ-ONLY · NO ACCOUNT NEEDED</span>}
    </header>
    <main className="container public-share-main">{children}</main>
  </div>;
}

/** Volume rows as a real table — name, format, shape, View. Long crop names wrap
 * in `.cell-name` (with a `title`) instead of pushing View off-screen. */
export function PublicShareVolumeTable({volumes, onOpen}: {volumes: ShareVolume[]; onOpen: (volumeId: number) => void}) {
  const [query, setQuery] = useState("");
  const searchable = volumes.length > 10;
  const needle = query.trim().toLowerCase();
  const rows = searchable && needle ? volumes.filter(volume => volume.name.toLowerCase().includes(needle)) : volumes;
  if (volumes.length === 0) return <div className="empty-state">No volumes are shared here.</div>;
  return <>
    {searchable && <input
      className="public-share-search"
      type="search"
      aria-label="Search volumes"
      placeholder={`Search ${volumes.length} volumes…`}
      value={query}
      onChange={event => setQuery(event.target.value)}
    />}
    <DatasetVolumesTable volumes={rows} actionLabel="View" actionAlign="center" action={(item) => {
      const volume = item as ShareVolume;
      // Primary (filled) rather than `secondary`: on an authenticated task row
      // View is the *secondary* action next to a filled Annotate, but a share
      // row has exactly one action, and rendering the row's only affordance in
      // the app's low-emphasis style left it near-invisible on a white row.
      return <div className="public-share-row-action">
        <button type="button" className="compact-button" onClick={() => onOpen(volume.id)}>View</button>
      </div>;
    }}/>
    {rows.length === 0 && <div className="empty-state">No volume matches “{query}”.</div>}
  </>;
}

/** Dataset index for a project-scope share: one row per dataset, plus the
 * ungrouped bucket when the project still has pre-dataset volumes. */
function PublicShareDatasetTable({share, onOpen}: {share: PublicShareData; onOpen: (dataset: DatasetKey) => void}) {
  const ungrouped = volumesIn(share, "ungrouped");
  const rows: Array<{key: DatasetKey; name: string; description: string; count: number}> = [
    ...share.datasets.map(dataset => ({
      key: dataset.id as DatasetKey,
      name: dataset.name,
      description: dataset.description,
      count: volumesIn(share, dataset.id).length,
    })),
    ...(ungrouped.length ? [{key: "ungrouped" as DatasetKey, name: "Ungrouped volumes", description: "Volumes registered before dataset grouping was available.", count: ungrouped.length}] : []),
  ];
  if (rows.length === 0) return <div className="empty-state">This share has no datasets yet.</div>;
  return <ul className="public-share-dataset-list" aria-label="Shared datasets">
    {rows.map(row => <li className="public-share-dataset-card" data-share-row key={String(row.key)}>
      <div className="public-share-dataset-copy">
        <h3 title={row.name}>{row.name}</h3>
        <p className={`public-share-dataset-description${row.description ? "" : " is-empty"}`}>
          {row.description || "No description"}
        </p>
      </div>
      <span className="public-share-volume-count" aria-label={`${row.count} ${row.count === 1 ? "volume" : "volumes"}`}>
        <strong>{row.count}</strong> {row.count === 1 ? "volume" : "volumes"}
      </span>
      {/* Filled, for the same reason as the volume row's View above. */}
      <div className="public-share-row-action">
        <button type="button" className="compact-button" onClick={() => onOpen(row.key)}>Open</button>
      </div>
    </li>)}
  </ul>;
}

export default function PublicShareBrowse({
  share,
  openDataset,
  onOpenDataset,
  onOpenVolume,
}: {
  share: PublicShareData;
  /** Null on a project share's dataset index; set once a dataset is open. */
  openDataset: DatasetKey | null;
  onOpenDataset: (dataset: DatasetKey | null) => void;
  onOpenVolume: (volumeId: number) => void;
}) {
  // A dataset-scope share is already "inside" its dataset — skip the index.
  const dataset = share.scope === "dataset" ? (share.dataset_id ?? "ungrouped") : openDataset;
  const datasetName = dataset === "ungrouped"
    ? "Ungrouped volumes"
    : share.datasets.find(row => row.id === dataset)?.name ?? share.dataset_name;
  const canGoUp = share.scope === "project" && dataset != null;

  return <PublicShareChrome scope={share.scope}>
    {/* Crumbs only once we are inside a dataset — at the index they would just
        repeat the project title in the heading below. */}
    {dataset != null && <nav className="public-share-crumbs" aria-label="Breadcrumb">
      {canGoUp
        ? <button type="button" className="crumb-link" onClick={() => onOpenDataset(null)}>{share.project_title}</button>
        : <span>{share.project_title}</span>}
      <span aria-hidden="true">›</span><span className="public-share-crumb-current">{datasetName}</span>
    </nav>}

    {dataset == null ? <>
      <div className="public-share-head">
        <h1>{share.project_title}</h1>
        <p className="muted">
          {share.datasets.length} dataset{share.datasets.length === 1 ? "" : "s"} · {share.volumes.length} volume{share.volumes.length === 1 ? "" : "s"} · shared by {share.created_by_username || "a manager"}
        </p>
      </div>
      <section className="section-block">
        <div className="section-heading"><h2>Datasets</h2><p className="muted">Open a dataset to see its volumes.</p></div>
        <PublicShareDatasetTable share={share} onOpen={onOpenDataset}/>
      </section>
    </> : <>
      <div className="public-share-head">
        <h1>{datasetName}</h1>
        <p className="muted">{share.project_title} · read-only view of shared volumes</p>
      </div>
      <section className="section-block">
        <div className="section-heading"><h2>Volumes</h2></div>
        <PublicShareVolumeTable volumes={volumesIn(share, dataset)} onOpen={onOpenVolume}/>
      </section>
    </>}
  </PublicShareChrome>;
}
