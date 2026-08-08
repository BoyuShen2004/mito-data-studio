import { useState } from "react";
import { Link } from "react-router-dom";
import {
  datasetDependents,
  deleteDataset,
  updateDataset,
  type Dataset,
} from "../api/datasets";
import type { DatasetMetadata } from "../types/project";
import type { Volume } from "../types/volume";
import { useAuth } from "../auth/AuthContext";
import { METADATA_FIELDS } from "../metadataFields";
import DeleteButton from "./DeleteButton";
import DatasetMeta from "./DatasetMeta";
import { DatasetVolumesTable } from "./VolumeMeta";

/** Datasets in a project, rendered as sections inside one card (not separate cards). */
export default function DatasetsCard({
  datasets,
  volumes,
  projectId,
  onChanged,
}: {
  datasets: Dataset[];
  volumes: Volume[];
  projectId: number;
  onChanged: () => void;
}) {
  const { isManager, isRequester } = useAuth();
  const canManage = isManager || isRequester;
  const [editing, setEditing] = useState<number | null>(null);

  return (
    <section className="section-block data-section">
      <div className="row spread">
        <h2 style={{ margin: 0 }}>Datasets &amp; volume pairs</h2>
        {canManage && <Link to={`/register-data?project=${projectId}`}>
          <button type="button" className="secondary">
            + Register more data
          </button>
        </Link>}
      </div>

      {datasets.length === 0 ? (
        <p className="muted" style={{ marginBottom: 0 }}>
          No datasets yet. {canManage ? <><Link to={`/register-data?project=${projectId}`}>Register data</Link> to add one.</> : ""}
        </p>
      ) : (
        <div className="dataset-sections">
          {datasets.map((ds) => {
            // Volumes registered before datasets existed have no dataset link;
            // show them under the dataset only when they genuinely belong to it.
            const own = volumes.filter((v) => v.dataset === ds.id);
            return (
              <section className="dataset-section" key={ds.id}>
                <div className="row spread">
                  <h4 className="dataset-section-title">
                    {ds.name}{" "}
                    <span className="muted" style={{ fontWeight: 400 }}>
                      · {own.length} volume pair{own.length === 1 ? "" : "s"}
                    </span>
                  </h4>
                  {canManage && <div className="row">
                    <button
                      type="button"
                      className="secondary"
                      onClick={() =>
                        setEditing(editing === ds.id ? null : ds.id)
                      }
                    >
                      {editing === ds.id ? "Close" : "Edit"}
                    </button>
                    <DeleteButton
                      label={`dataset "${ds.name}"`}
                      dependents={() => datasetDependents(ds.id)}
                      onDelete={(force) => deleteDataset(ds.id, force)}
                      onDone={onChanged}
                    />
                  </div>}
                </div>

                {ds.description && <p className="muted">{ds.description}</p>}
                {editing === ds.id && (
                  <DatasetEditForm
                    dataset={ds}
                    onSaved={() => {
                      setEditing(null);
                      onChanged();
                    }}
                  />
                )}

                {Object.keys(ds.metadata || {}).length > 0 && (
                  <DatasetMeta
                    metadata={ds.metadata}
                    title="Dataset metadata"
                  />
                )}

                {own.length > 0 && <DatasetVolumesTable
                  volumes={own}
                  streaming={canManage ? (item) => {
                    const v = item as Volume;
                    return <StreamingStatus status={v.streaming_status} region={v.region_streaming_status}/>;
                  } : undefined}
                  actionLabel="Details"
                  action={(item) => <Link to={`/volumes/${(item as Volume).id}`}>Details</Link>}
                />}
              </section>
            );
          })}
        </div>
      )}
    </section>
  );
}

function DatasetEditForm({
  dataset,
  onSaved,
}: {
  dataset: Dataset;
  onSaved: () => void;
}) {
  const [name, setName] = useState(dataset.name);
  const [description, setDescription] = useState(dataset.description);
  const [imageDir, setImageDir] = useState(dataset.image_directory);
  const [regionMaskDir, setRegionMaskDir] = useState(dataset.region_mask_directory);
  const [maskDir, setMaskDir] = useState(dataset.mask_directory);
  const [meta, setMeta] = useState<DatasetMetadata>(dataset.metadata ?? {});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      // Send blanks as null so clearing a field removes it server-side
      // rather than merging an empty string back in.
      const cleaned: DatasetMetadata = {};
      for (const f of METADATA_FIELDS) {
        const value = meta[f.key];
        cleaned[f.key] =
          typeof value === "string" && value.trim() ? value.trim() : null;
      }
      await updateDataset(dataset.id, {
        name,
        description,
        image_directory: imageDir,
        region_mask_directory: regionMaskDir,
        mask_directory: maskDir,
        metadata: cleaned,
      });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="edit-form">
      {error && <div className="error">{error}</div>}
      <div className="row fields">
        <label className="field" style={{ flex: 1 }}>
          <span>Dataset name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field" style={{ flex: 2 }}>
          <span>Description</span>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
      </div>
      <div className="row fields">
        <label className="field">
          <span>Raw images</span>
          <input value={imageDir} onChange={(e) => setImageDir(e.target.value)} />
        </label>
        <label className="field">
          <span>Editable starting labels (optional)</span>
          <input value={maskDir} onChange={(e) => setMaskDir(e.target.value)} />
        </label>
        <label className="field">
          <span>Region masks (optional, read-only)</span>
          <input
            value={regionMaskDir}
            onChange={(e) => setRegionMaskDir(e.target.value)}
          />
        </label>
      </div>
      <div className="grid">
        {METADATA_FIELDS.map((f) => (
          <label className="field" key={String(f.key)}>
            <span>{f.label}</span>
            <input
              value={(meta[f.key] as string) ?? ""}
              onChange={(e) =>
                setMeta((m) => ({ ...m, [f.key]: e.target.value }))
              }
            />
          </label>
        ))}
      </div>
      <button type="button" onClick={save} disabled={busy}>
        {busy ? "Saving…" : "Save dataset"}
      </button>
    </div>
  );
}

function LayerBadge({
  label,
  status,
}: {
  label: string;
  status: Volume["streaming_status"];
}) {
  const [tone, text] =
    status === "ready"
      ? ["badge-green", "ready"]
      : status === "building"
        ? ["badge-blue", "building…"]
        : status === "failed"
          ? ["badge-red", "failed"]
          : ["badge-gray", "not built"];
  // The layer name is the badge's prefix rather than a second column: the
  // volume-pairs table's widths already sum to ~100%, and two layers are a
  // detail of one "Streaming" answer.
  return (
    <span className={`badge ${tone}`} title={`${label}: ${text}`}>
      {label} {text}
    </span>
  );
}

function StreamingStatus({
  status,
  region,
}: {
  status: Volume["streaming_status"];
  region?: Volume["region_streaming_status"];
}) {
  if (!region || region === "absent") {
    return <LayerBadge label="Image" status={status} />;
  }
  return (
    <span className="streaming-dual">
      <LayerBadge label="Image" status={status} />
      <LayerBadge label="Region" status={region} />
    </span>
  );
}
