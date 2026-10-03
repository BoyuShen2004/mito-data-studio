import { useEffect, useState } from "react";
import { getMeasurements, measurementCSV, runMeasurements, type MeasurementJob, type MeasurementSource } from "../api/measurements";
import type { Volume } from "../types/volume";

const active = (job: MeasurementJob | null) =>
  job != null && ["queued", "submitted", "running"].includes(job.status);
const PAGE_SIZE = 50;

export default function MitoMeasurements({ volume, canRun, runBlocked = false }: { volume: Volume; canRun: boolean; runBlocked?: boolean }) {
  const [source, setSource] = useState<MeasurementSource>("official");
  return (
    <section className="section-block" aria-label="Mitochondria measurements">
      <div className="section-heading">
        <h2>Mitochondria measurements</h2>
        <p className="muted">Whole volume; no task-range or ROI clipping.</p>
      </div>
      <label className="row">
        Label source
        <select aria-label="Label source" value={source} onChange={event => setSource(event.target.value as MeasurementSource)}>
          <option value="official">Official label</option>
          <option value="working">Saved working draft</option>
        </select>
      </label>
      <MeasurementRun key={`${volume.id}-${source}`} volume={volume} source={source} canRun={canRun} runBlocked={runBlocked} />
    </section>
  );
}

function MeasurementRun({ volume, source, canRun, runBlocked }: { volume: Volume; source: MeasurementSource; canRun: boolean; runBlocked: boolean }) {
  const [job, setJob] = useState<MeasurementJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [page, setPage] = useState(0);
  const running = active(job);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const response = await getMeasurements(volume.id, source);
        if (!alive) return;
        setJob(response.job);
        setPage(0);
        setError("");
        if (active(response.job)) timer = setTimeout(load, 3000);
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : "Could not load measurements.");
      } finally {
        if (alive) setLoading(false);
      }
    };
    void load();
    return () => { alive = false; clearTimeout(timer); };
  }, [volume.id, source, refresh]);

  const spacing = [volume.voxel_size_z, volume.voxel_size_y, volume.voxel_size_x];
  const validSpacing = spacing.every(value => value != null && Number.isFinite(value) && value > 0);
  const unavailable = source === "official" && !volume.has_label;
  const run = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await runMeasurements(volume.id, source);
      setJob(response.job);
      setPage(0);
      setRefresh(value => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not queue measurements.");
    } finally {
      setBusy(false);
    }
  };
  const download = () => {
    if (!job?.result) return;
    const url = URL.createObjectURL(new Blob([measurementCSV(volume.id, job)], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `mitochondria-volume-${volume.id}-${source}-run-${job.id}.csv`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const result = job?.result;
  const rows = result?.rows ?? [];
  return (
    <>
      <p className="muted">
        {source === "working"
          ? "Saved draft; unsaved canvas edits are excluded."
          : "Official label; excludes drafts and submission snapshots."}
      </p>
      {!validSpacing && <p role="status">Voxel size required: Z, Y, X (nm).</p>}
      {unavailable && <p role="status">No official label is registered.</p>}
      <div className="row">
        {canRun ? (
          <button type="button" disabled={loading || busy || running || runBlocked || !validSpacing || unavailable} onClick={() => void run()}>
            {busy ? "Queueing…" : running ? "Measurement in progress…" : "Run measurements"}
          </button>
        ) : <span className="muted">Only managers can start a run.</span>}
        <button type="button" className="secondary" disabled={busy} onClick={() => setRefresh(value => value + 1)}>Refresh results</button>
        {result && <button type="button" className="secondary" onClick={download}>Export CSV</button>}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {loading ? <p role="status">Loading measurements…</p> : job ? (
        <p role="status">Run #{job.id} · {job.status}{job.finished_at ? ` · ${new Date(job.finished_at).toLocaleString()}` : ""}</p>
      ) : !error ? <p className="muted">No runs for this label source.</p> : null}
      {job?.error && <p className="error" role="alert">{job.error}</p>}
      {result && (
        <>
          {!job.is_current && <p role="status">Outdated result: labels or voxel size changed. Run again to update.</p>}
          <p className="muted">
            {result.method} · Voxel size (Z, Y, X): {result.voxel_size_nm_zyx.join(", ")} nm · {rows.length} labels.
            {" "}Components below {result.dust_size_voxels} voxels count toward volume, but not skeleton length.
            {" "}Zero length may indicate filtering, not zero volume.
          </p>
          {rows.length === 0 ? <p>No nonzero labels were found.</p> : (
            <>
              <div className="measurement-table">
                <table>
                  <caption className="sr-only">Mitochondria measurements from {source} labels</caption>
                  <thead><tr><th>Label ID</th><th>Voxel count</th><th>Volume (µm³)</th><th>Skeleton cable length (µm)</th></tr></thead>
                  <tbody>{rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map(row => (
                    <tr key={row.label_id}>
                      <td>{row.label_id}</td><td>{row.voxel_count.toLocaleString()}</td>
                      <td>{Number(row.volume_um3.toPrecision(6))}</td><td>{Number(row.skeleton_length_um.toPrecision(6))}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              {rows.length > PAGE_SIZE && <div className="row">
                <button type="button" className="secondary" disabled={page === 0} onClick={() => setPage(value => value - 1)}>Previous</button>
                <span>Page {page + 1} of {Math.ceil(rows.length / PAGE_SIZE)}</span>
                <button type="button" className="secondary" disabled={(page + 1) * PAGE_SIZE >= rows.length} onClick={() => setPage(value => value + 1)}>Next</button>
              </div>}
            </>
          )}
        </>
      )}
    </>
  );
}
