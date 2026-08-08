import { useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import {
  scanDataSources,
  registerData,
  getRegistrationSnapshot,
  reconcileRegistration,
  type ScanResult,
  type RegisterDataPair,
} from "../api/registerData";
import { ApiError } from "../api/client";
import { listProjects } from "../api/projects";
import { useAsync } from "../hooks/useAsync";
import type { DatasetMetadata } from "../types/project";
import type { LabelType } from "../types";
import type { Volume } from "../types/volume";
import { DatasetVolumesTable } from "../components/VolumeMeta";

const METADATA_FIELDS: { key: keyof DatasetMetadata; label: string }[] = [
  { key: "organism", label: "Organism / species" },
  { key: "tissue", label: "Tissue or organ" },
  { key: "cell_type", label: "Cell type" },
  { key: "imaging_modality", label: "Imaging modality" },
  { key: "imaging_instrument", label: "Imaging instrument / microscope" },
  { key: "experimental_condition", label: "Experimental condition" },
  { key: "sample_condition", label: "Sample condition" },
  { key: "dataset_source", label: "Dataset source" },
  { key: "publication", label: "Publication / reference" },
];

const LABEL_TYPES_WITH_MASK: LabelType[] = ["partial", "prediction"];

const NO_MASK = ""; // sentinel for the "image only" option

interface Row {
  image: string;
  region_mask: string;
  mask: string; // "" = image only
  case: string;
  /** Optional rename; blank keeps the case/file id as volume name. */
  name: string;
  selected: boolean;
}

// One directory configured for registration: it becomes a single dataset
// holding all of its selected volume pairs. Several of these can be queued up so
// many directories register into the same project in one go.
// Each scanned row is renamed individually (``Row.name``); there is no shared
// parent "source volume" / crop grouping in the product model.
interface StagedDataset {
  dataset: string;
  image_directory: string;
  region_mask_directory: string;
  mask_directory: string;
  label_type: LabelType;
  metadata: DatasetMetadata;
  pairs: RegisterDataPair[];
}

export default function RegisterDataPage() {
  const { isManager } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  // Data is registered *into* a project, which must already exist: arriving
  // from "New project" or a project's "Register more data" preselects it.
  const [projectId, setProjectId] = useState(params.get("project") ?? "");
  const projects = useAsync(() => listProjects(), []);
  const hasProjects = (projects.data ?? []).length > 0;

  const [dataset, setDataset] = useState("");
  const [imageDir, setImageDir] = useState("");
  const [regionMaskDir, setRegionMaskDir] = useState("");
  const [maskDir, setMaskDir] = useState("");
  const [labelType, setLabelType] = useState<LabelType>("none");
  const [metadata, setMetadata] = useState<DatasetMetadata>({});
  const [description, setDescription] = useState("");
  const [notes, setNotes] = useState("");

  const [scan, setScan] = useState<ScanResult | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  /** Row index whose Rename field is open; null = all collapsed. */
  const [renamingIndex, setRenamingIndex] = useState<number | null>(null);
  const [scanning, setScanning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [registrationNotice, setRegistrationNotice] = useState<string | null>(null);
  // Ignore an older scan response if the user starts another scan before it
  // completes. HPC directory scans can have noticeably different latencies.
  const scanRequest = useRef(0);

  // Directories queued up to register together, each becoming its own dataset.
  const [staged, setStaged] = useState<StagedDataset[]>([]);
  // Result of the last "Register" action (one entry per dataset registered),
  // shown as a success banner. A project can hold many datasets, so we stay on
  // the page afterwards instead of navigating straight to the project.
  const [lastResult, setLastResult] = useState<
    { dataset: string; count: number; volumes: Volume[] }[] | null
  >(null);

  // Selected rows with editable labels require partial|prediction. Changes to
  // this value happen in the explicit scan/row handlers below, not an effect.
  const anySelectedMask = useMemo(
    () => rows.some((r) => r.selected && r.mask !== NO_MASK),
    [rows],
  );
  const allowedLabelTypes = anySelectedMask ? LABEL_TYPES_WITH_MASK : (["none"] as LabelType[]);

  const setMeta = (key: keyof DatasetMetadata, value: string) =>
    setMetadata((m) => ({ ...m, [key]: value }));

  const buildRows = (res: ScanResult): Row[] => [
    ...res.pairs.map((p) => ({
      image: p.image,
      region_mask: "",
      mask: NO_MASK,
      case: p.case,
      name: "",
      selected: true,
    })),
    // Every scanned image file is a row; region/label stay empty for manual pick.
    ...res.unmatched_images.map((name) => ({
      image: name,
      region_mask: "",
      mask: NO_MASK,
      case: name,
      name: "",
      selected: true,
    })),
  ];

  // dataset.json already records what a requester would otherwise retype.
  const prefillFromManifest = (res: ScanResult) => {
    const meta = res.dataset_metadata || {};
    const text = (v: unknown) => (typeof v === "string" ? v : "");
    setMetadata((m) => ({
      ...m,
      ...(text(meta.publication) && !m.publication
        ? { publication: text(meta.publication) }
        : {}),
      ...(text(meta.dataset_source) && !m.dataset_source
        ? { dataset_source: text(meta.dataset_source) }
        : {}),
    }));
    setDescription((d) => d || text(meta.description));
  };

  const invalidateScan = () => {
    scanRequest.current += 1;
    setScanning(false);
    setScan(null);
    setRows([]);
    setRenamingIndex(null);
    setLabelType("none");
    setError(null);
  };

  // Clear the dataset-specific inputs so the same project stays selected and the
  // form is ready for the next dataset.
  const resetForAnother = () => {
    setDataset("");
    setImageDir("");
    setRegionMaskDir("");
    setMaskDir("");
    setLabelType("none");
    setMetadata({});
    setDescription("");
    setNotes("");
    setScan(null);
    setRows([]);
    setError(null);
  };

  const runScan = async (image: string, mask: string, regionMask: string) => {
    const requestId = ++scanRequest.current;
    setScanning(true);
    setError(null);
    setLastResult(null);
    try {
      const res = await scanDataSources(image, mask, regionMask);
      if (requestId !== scanRequest.current) return;
      const nextRows = buildRows(res);
      setScan(res);
      setRows(nextRows);
      setRenamingIndex(null);
      const hasEditableLabels = nextRows.some((row) => Boolean(row.mask));
      const nextLabelType = hasEditableLabels
        ? (labelType === "partial" ? "partial" : "prediction")
        : "none";
      setLabelType(nextLabelType);
      prefillFromManifest(res);
    } catch (e) {
      if (requestId !== scanRequest.current) return;
      setError(e instanceof Error ? e.message : "Scan failed");
      setScan(null);
      setRows([]);
    } finally {
      if (requestId === scanRequest.current) setScanning(false);
    }
  };

  // Quick-pick sibling folders used to live here; removed — type paths directly
  // or pair files in the matched-volumes table.

  const update = (i: number, patch: Partial<Row>) => {
    const next = rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r));
    setRows(next);
    const nextHasMask = next.some((r) => r.selected && r.mask !== NO_MASK);
    if (nextHasMask && labelType === "none") {
      setLabelType("prediction");
    } else if (!nextHasMask && labelType !== "none") {
      setLabelType("none");
    }
  };

  const setAll = (selected: boolean) => {
    const next = rows.map((r) => ({ ...r, selected }));
    setRows(next);
    const nextHasMask = next.some((r) => r.selected && r.mask !== NO_MASK);
    const nextLabelType = nextHasMask
      ? (labelType === "partial" ? "partial" : "prediction")
      : "none";
    setLabelType(nextLabelType);
  };

  // Per-column mutual exclusion: a region mask or editable-label file already
  // chosen on another row is omitted from this row's dropdown (current value
  // always stays visible so the <select> remains valid). Prevents accidentally
  // pairing the same label file to multiple raw volumes.
  const freeMasks = (row: Row, rowIndex: number): string[] => {
    if (!scan) return [];
    const taken = new Set(
      rows
        .filter((_, idx) => idx !== rowIndex)
        .map((r) => r.mask)
        .filter((m) => m !== NO_MASK),
    );
    return scan.mask_files
      .map((f) => f.name)
      .filter((name) => name === row.mask || !taken.has(name));
  };

  const freeRegionMasks = (row: Row, rowIndex: number): string[] => {
    if (!scan) return [];
    const taken = new Set(
      rows
        .filter((_, idx) => idx !== rowIndex)
        .map((r) => r.region_mask)
        .filter((m) => m !== ""),
    );
    return scan.region_mask_files
      .map((f) => f.name)
      .filter((name) => name === row.region_mask || !taken.has(name));
  };

  // Collapse the current metadata inputs + manifest facts into what travels
  // with a dataset.
  const buildMeta = (): DatasetMetadata => {
    const meta: DatasetMetadata = {};
    for (const [k, v] of Object.entries(metadata)) {
      if (typeof v === "string" && v.trim()) meta[k] = v.trim();
    }
    if (description.trim()) meta.description = description.trim();
    if (notes.trim()) meta.notes = notes.trim();
    const manifestMeta = scan?.dataset_metadata || {};
    if (manifestMeta.label_classes) meta.label_classes = manifestMeta.label_classes;
    if (manifestMeta.channel_names) meta.channel_names = manifestMeta.channel_names;
    if (scan?.split) meta.split = scan.split;
    return meta;
  };

  // Turn the currently-scanned directory into a stageable dataset, or set an
  // error and return null when it is incomplete.
  const buildCurrentEntry = (): StagedDataset | null => {
    if (!scan) {
      setError("Scan a directory first.");
      return null;
    }
    if (!imageDir.trim()) {
      setError("Enter a raw image directory and scan it first.");
      return null;
    }
    const chosen = rows.filter((r) => r.selected);
    if (chosen.length === 0) {
      setError("Select at least one image to register.");
      return null;
    }
    if (!dataset.trim()) {
      setError("Enter a dataset name for this directory.");
      return null;
    }
    if (anySelectedMask && !LABEL_TYPES_WITH_MASK.includes(labelType)) {
      setError("Choose partial or prediction for the selected editable labels.");
      return null;
    }
    return {
      dataset: dataset.trim(),
      image_directory: imageDir,
      region_mask_directory: regionMaskDir,
      mask_directory: maskDir,
      label_type: labelType,
      metadata: buildMeta(),
      pairs: chosen.map((r) => ({
        image: r.image,
        region_mask: r.region_mask || undefined,
        mask: r.mask || undefined,
        name: r.name || undefined,
      })),
    };
  };

  // Is the current form a complete directory ready to stage or register?
  const currentReady =
    !!scan && !!imageDir.trim() && rows.some((r) => r.selected) &&
    !!dataset.trim();

  // Queue the current directory and reset the form for the next one.
  const stageCurrent = () => {
    setError(null);
    const entry = buildCurrentEntry();
    if (!entry) return;
    setStaged((s) => [...s, entry]);
    resetForAnother();
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const removeStaged = (index: number) =>
    setStaged((s) => s.filter((_, i) => i !== index));

  // Register every queued directory plus the current one (if ready) into the
  // chosen project — one dataset per directory, in one action.
  const registerAll = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setRegistrationNotice(null);
    if (!projectId) {
      setError("Choose the project to register this data into.");
      return;
    }
    const queuedEntries = [...staged];
    const entries = [...queuedEntries];
    let currentEntry: StagedDataset | null = null;
    if (scan && rows.some((r) => r.selected)) {
      currentEntry = buildCurrentEntry();
      if (!currentEntry) return; // validation error already surfaced
      entries.push(currentEntry);
    }
    if (entries.length === 0) {
      setError("Add at least one directory to register.");
      return;
    }

    setBusy(true);
    const succeeded: { dataset: string; count: number; volumes: Volume[] }[] = [];
    const failed: { dataset: string; message: string }[] = [];
    const inconclusive: string[] = [];
    const failedEntries = new Set<StagedDataset>();
    let currentSucceeded = false;
    let currentInconclusive = false;
    let lastProjectId = Number(projectId);
    for (const entry of entries) {
      const targetProjectId = Number(projectId);
      const before = await getRegistrationSnapshot(targetProjectId, entry.dataset);
      try {
        const res = await registerData({
          dataset: entry.dataset,
          project: Number(projectId),
          image_directory: entry.image_directory,
          region_mask_directory: entry.region_mask_directory || undefined,
          mask_directory: entry.mask_directory || undefined,
          label_type: entry.label_type,
          metadata: entry.metadata,
          pairs: entry.pairs,
        });
        lastProjectId = res.project.id;
        succeeded.push({ dataset: entry.dataset, count: res.volumes.length, volumes: res.volumes });
        if (entry === currentEntry) currentSucceeded = true;
      } catch (err) {
        // An HTTP response proves the server rejected the request. A raw fetch
        // rejection/abort does not: ingress may have lost the response while
        // gunicorn continued and committed the registration.
        if (!(err instanceof ApiError)) {
          setRegistrationNotice(`Request interrupted; verifying ${entry.dataset}…`);
          const reconciled = await reconcileRegistration(
            targetProjectId,
            entry.dataset,
            entry.pairs.length,
            before,
          );
          if (reconciled.status === "complete") {
            succeeded.push({
              dataset: entry.dataset,
              count: reconciled.volumes.length,
              volumes: reconciled.volumes,
            });
            if (entry === currentEntry) currentSucceeded = true;
            continue;
          }
          if (reconciled.status === "inconclusive") {
            inconclusive.push(entry.dataset);
            if (entry === currentEntry) currentInconclusive = true;
            continue;
          }
        }
        failedEntries.add(entry);
        failed.push({
          dataset: entry.dataset,
          message: err instanceof Error ? err.message : "registration failed",
        });
      }
    }
    setBusy(false);
    setProjectId(String(lastProjectId));

    // Keep only the exact queued entries that failed. Dataset names may be
    // reused, so filtering by name can retain a successful entry by mistake.
    setStaged(queuedEntries.filter((entry) => failedEntries.has(entry)));

    if (succeeded.length > 0) {
      setLastResult(succeeded);
      // Do not erase an unqueued current form when only queued entries
      // succeeded, or when this current entry itself failed.
      if (currentSucceeded) resetForAnother();
    }
    // An inconclusive transport result must not leave a one-click retry that
    // could duplicate a registration which actually committed server-side.
    if (currentInconclusive) resetForAnother();
    if (failed.length > 0) {
      setError(
        "Some directories could not be registered: " +
          failed.map((f) => `${f.dataset} (${f.message})`).join("; "),
      );
    }
    setRegistrationNotice(
      inconclusive.length > 0
        ? `Registration may have completed for ${inconclusive.join(", ")} — check the project Data tab before trying again.`
        : null,
    );
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const stagedVolumeCount = staged.reduce((n, d) => n + d.pairs.length, 0);
  const registerCount = staged.length + (currentReady ? 1 : 0);

  // Wait for the project list before rendering. A <select> whose value is set
  // (from ?project=) before its <option>s exist cannot match it, and silently
  // falls back to blank — which would drop the preselection handed over by
  // "Create project & register data".
  if (projects.loading) return <p className="muted">Loading…</p>;

  const selectedCount = rows.filter((r) => r.selected).length;
  const matchedCount = rows.filter((r) => Boolean(r.mask)).length;
  const regionMatchedCount = rows.filter((r) => Boolean(r.region_mask)).length;

  return (
    <>
      <div className="row spread">
        <h1>Register Data</h1>
        <span className="muted">
          {isManager ? "Manager" : "Requester"} · references HPC files (no upload)
        </span>
      </div>

      <p className="muted">Ingest data paths and set imaging metadata and label Type here. Create a team and pick its annotators in People.</p>

      {error && <div className="error">{error}</div>}
      {registrationNotice && <div className="info" role="status">{registrationNotice}</div>}

      {lastResult && lastResult.length > 0 && (
        <div className="card">
          <div className="row spread">
            <h3 style={{ margin: 0 }}>
              {lastResult.map((d) => `${d.dataset} (${d.count})`).join(", ")}
            </h3>
            <div className="row">
              <button
                type="button"
                className="secondary"
                onClick={() => setLastResult(null)}
              >
                Register more
              </button>
              {projectId && (
                <button
                  type="button"
                  onClick={() => navigate(`/projects/${projectId}`)}
                >
                  Go to project →
                </button>
              )}
            </div>
          </div>
          <p className="muted" style={{ marginBottom: 0 }}>
            {lastResult.map((d) => `${d.dataset} (${d.count})`).join(", ")}. A
            project can hold many datasets — queue more directories below, or open
            the project when you are done.
          </p>
          <DatasetVolumesTable volumes={lastResult.flatMap((result) => result.volumes)} />
        </div>
      )}

      {staged.length > 0 && (
        <div className="card" style={{ borderColor: "var(--accent)" }}>
          <h3 style={{ marginTop: 0 }}>
            Queued to register · {staged.length} director
            {staged.length === 1 ? "y" : "ies"} / {stagedVolumeCount} volume
            {stagedVolumeCount === 1 ? "" : "s"}
          </h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th>Layer directories</th>
                  <th>Volumes</th>
                  <th>Label type</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {staged.map((d, i) => (
                  <tr key={`${d.dataset}:${i}`}>
                    <td>{d.dataset}</td>
                    <td className="mono-cell">
                      <div>Raw · {d.image_directory}</div>
                      {d.mask_directory && <div>Labels · {d.mask_directory}</div>}
                      {d.region_mask_directory && <div>Region · {d.region_mask_directory}</div>}
                    </td>
                    <td>{d.pairs.length}</td>
                    <td>{d.label_type}</td>
                    <td>
                      <button
                        type="button"
                        className="secondary"
                        onClick={() => removeStaged(i)}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ marginBottom: 0 }}>
            Configure another directory below and add it, or register the queue
            now.
          </p>
        </div>
      )}

      {!hasProjects && (
        <div className="card">
          <h3>Create a project first</h3>
          <p className="muted">
            Data belongs to a project, and you do not have one yet. Projects
            describe the work — what is annotated, by when.
          </p>
          <Link to="/projects/new">
            <button type="button">+ New project</button>
          </Link>
        </div>
      )}

      <form onSubmit={registerAll}>
        <div className="card">
          <div className="row spread register-dirs-head">
            <h3>Directories</h3>
            <button
              type="button"
              className="secondary register-dirs-scan"
              onClick={() => runScan(imageDir, maskDir, regionMaskDir)}
              disabled={scanning || !imageDir.trim()}
            >
              {scanning ? "Scanning…" : "Scan"}
            </button>
          </div>
          <div className="register-align">
            <label className="field">
              <span>
                <strong>1 · Raw image directory</strong> *
              </span>
              <input
                value={imageDir}
                onChange={(e) => {
                  setImageDir(e.target.value);
                  invalidateScan();
                }}
                placeholder="/path/to/images"
              />
            </label>
            <label className="field">
              <span>
                <strong>2 · Region mask directory</strong> (optional, read-only)
              </span>
              <input
                value={regionMaskDir}
                onChange={(e) => {
                  setRegionMaskDir(e.target.value);
                  invalidateScan();
                }}
                placeholder="/path/to/region-masks"
              />
            </label>
            <label className="field">
              <span>
                <strong>3 · Editable labels directory</strong> (optional)
              </span>
              <input
                value={maskDir}
                onChange={(e) => {
                  setMaskDir(e.target.value);
                  invalidateScan();
                }}
                placeholder="/path/to/starting-labels"
              />
            </label>
          </div>
        </div>

        <div className="card">
          <div className="row spread">
            <h3>Scanned volumes</h3>
            <div className="row">
              {scan &&
                selectedCount > 0 &&
                (matchedCount > 0 || regionMatchedCount > 0) &&
                scan.pairing_source === "dataset.json" && (
                  <span className="badge badge-green">paired from dataset.json</span>
                )}
              {scan?.split && (
                <span className="badge badge-gray">{scan.split} split</span>
              )}
            </div>
          </div>

          {scan && rows.length > 0 && (
            <div className="row spread" style={{ marginTop: "0.75rem" }}>
              <label className="field" style={{ marginBottom: 0 }}>
                <span>Label type *</span>
                <select
                  value={labelType}
                  onChange={(e) => setLabelType(e.target.value as LabelType)}
                  disabled={allowedLabelTypes.length === 1}
                >
                  {allowedLabelTypes.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              <div className="row" style={{ alignSelf: "flex-end" }}>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setAll(true)}
                >
                  Select all
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setAll(false)}
                >
                  Clear
                </button>
              </div>
            </div>
          )}

          <div style={{ marginTop: "0.75rem" }}>
            <div className="register-align register-align-head">
              <div>Raw volume</div>
              <div>Region mask (read-only)</div>
              <div>Editable labels</div>
            </div>
            {!scan || rows.length === 0 ? (
              <div
                className="register-align register-volumes-empty"
                aria-hidden
              />
            ) : (
              rows.map((r, i) => (
                <div
                  key={`${r.image}:${i}`}
                  className="register-align register-align-row"
                >
                  <div className="register-volume-stack">
                    <div className="register-volume-primary">
                      <input
                        type="checkbox"
                        checked={r.selected}
                        onChange={() => update(i, { selected: !r.selected })}
                      />
                      <div className="mono-cell">{r.image}</div>
                      <button
                        type="button"
                        className="secondary register-volume-edit"
                        disabled={!r.selected}
                        aria-expanded={renamingIndex === i}
                        onClick={() =>
                          setRenamingIndex((cur) => (cur === i ? null : i))
                        }
                      >
                        {renamingIndex === i ? "Done" : "Edit"}
                      </button>
                    </div>
                    {renamingIndex === i && (
                      <label className="field register-volume-rename">
                        <span>Rename volume (optional)</span>
                        <input
                          aria-label="Rename volume (optional)"
                          value={r.name}
                          disabled={!r.selected}
                          onChange={(e) =>
                            update(i, { name: e.target.value })
                          }
                          placeholder="Leave blank to use the file name"
                          autoFocus
                        />
                      </label>
                    )}
                  </div>
                  <div className="register-volume-stack">
                    <div className="register-volume-primary">
                      {!scan.region_mask_directory &&
                      scan.region_mask_files.length === 0 ? (
                        <span className="muted">—</span>
                      ) : (
                        <select
                          value={r.region_mask}
                          disabled={!r.selected}
                          onChange={(e) =>
                            update(i, { region_mask: e.target.value })
                          }
                        >
                          <option value="">— none —</option>
                          {freeRegionMasks(r, i).map((f) => (
                            <option key={f} value={f}>
                              {f}
                            </option>
                          ))}
                        </select>
                      )}
                    </div>
                  </div>
                  <div className="register-volume-stack">
                    <div className="register-volume-primary">
                      {!scan.mask_directory && scan.mask_files.length === 0 ? (
                        <span className="muted">—</span>
                      ) : (
                        <select
                          value={r.mask}
                          disabled={!r.selected}
                          onChange={(e) =>
                            update(i, { mask: e.target.value })
                          }
                        >
                          <option value={NO_MASK}>— image only —</option>
                          {freeMasks(r, i).map((f) => (
                            <option key={f} value={f}>
                              {f}
                            </option>
                          ))}
                        </select>
                      )}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
        <div className="card">
          <h3>Project &amp; dataset</h3>
          {/* `.row.fields` gives each field `flex: 1 1 0`, so the two that are
              left split the full width instead of leaving the removed Volume
              name field's slot empty. */}
          <div className="row fields">
            <label className="field">
              <span>Project *</span>
              <select
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
              >
                <option value="">— select a project —</option>
                {(projects.data ?? []).map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    {p.title}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Dataset name *</span>
              <input
                value={dataset}
                onChange={(e) => setDataset(e.target.value)}
                placeholder="Dataset name"
              />
            </label>
          </div>
          <p className="muted">
            Register into an existing project, or{" "}
            <Link to="/projects/new">start a new project</Link>. Reusing a
            dataset name adds volume pairs to that dataset. Each volume is
            named via <strong>Edit</strong> → Rename in the list above, or by
            its file name when left blank.
          </p>
        </div>

        <div className="card">
          <h3>Metadata (optional)</h3>
          <p className="muted">
            Only details that cannot be derived from the files.
            {scan?.manifest_path
              ? " Some fields were prefilled from dataset.json."
              : " All fields are optional."}
          </p>
          <div className="grid">
            {METADATA_FIELDS.map((f) => (
              <label className="field" key={f.key}>
                <span>{f.label}</span>
                <input
                  value={(metadata[f.key] as string) ?? ""}
                  onChange={(e) => setMeta(f.key, e.target.value)}
                />
              </label>
            ))}
          </div>
          <label className="field">
            <span>Description</span>
            <textarea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Notes</span>
            <textarea
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </label>
        </div>

        <div className="row spread">
          <button
            type="button"
            className="secondary"
            onClick={stageCurrent}
            disabled={busy || !currentReady}
            title={
              currentReady
                ? "Queue this directory and start another"
                : "Scan a directory and name the dataset first"
            }
          >
            + Add another directory
          </button>
          <button
            type="submit"
            disabled={busy || registerCount === 0 || !projectId}
            title={!projectId ? "Choose a project before registering" : undefined}
          >
            {busy
              ? "Registering…"
              : `Register ${registerCount} dataset${
                  registerCount === 1 ? "" : "s"
                }`}
          </button>
        </div>
      </form>
    </>
  );
}
