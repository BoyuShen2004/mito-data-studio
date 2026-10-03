import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { editVolume } from "../api/volumes";
import type { Volume } from "../types/volume";
import MitoMeasurements from "./MitoMeasurements";

export default function ProjectMeasurements({ volumes, loading, error, canRun, onSaved }: {
  volumes: Volume[];
  loading: boolean;
  error: string | null;
  canRun: boolean;
  onSaved: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const selected = volumes.find(volume => String(volume.id) === params.get("volume")) ?? volumes[0];
  return (
    <section aria-label="Project measurements">
      <div className="section-heading">
        <h2>Measurements</h2>
        <p className="muted">Select a volume to configure, run and export its measurements. Runs and results stay separate for each volume and label source.</p>
      </div>
      {loading ? <p>Loading volumes…</p> : error ? <p className="error" role="alert">{error}</p> : !selected ? (
        <p>No volumes registered in this project.</p>
      ) : <>
        <label className="field">
          <span>Volume</span>
          <select aria-label="Volume" value={selected.id} onChange={event => setParams({ tab: "measurements", volume: event.target.value })}>
            {volumes.map(volume => <option key={volume.id} value={volume.id}>
              {volume.dataset_name ? `${volume.dataset_name} / ` : ""}{volume.name} · #{volume.id}
            </option>)}
          </select>
        </label>
        <VolumeMeasurements key={`${selected.id}-${selected.voxel_size_z}-${selected.voxel_size_y}-${selected.voxel_size_x}`} volume={selected} canRun={canRun} onSaved={onSaved} />
      </>}
    </section>
  );
}

function VolumeMeasurements({ volume, canRun, onSaved }: { volume: Volume; canRun: boolean; onSaved: () => void }) {
  const [current, setCurrent] = useState(volume);
  const [spacing, setSpacing] = useState([volume.voxel_size_z, volume.voxel_size_y, volume.voxel_size_x].map(value => value == null ? "" : String(value)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const previous = [current.voxel_size_z, current.voxel_size_y, current.voxel_size_x].map(value => value == null ? "" : String(value));
  const dirty = spacing.some((value, index) => value !== previous[index]);
  const valid = spacing.every(value => value.trim() !== "" && Number.isFinite(Number(value)) && Number(value) > 0);
  const save = async () => {
    if (!valid || !dirty) return;
    setBusy(true); setError("");
    try {
      const updated = await editVolume(volume.id, {
        voxel_size_z: Number(spacing[0]), voxel_size_y: Number(spacing[1]), voxel_size_x: Number(spacing[2]),
      });
      setCurrent(updated); setSaved(true); onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save voxel size.");
    } finally { setBusy(false); }
  };
  return <>
    <section className="section-block" aria-label="Measurement voxel size">
      <h3>Voxel size (nm)</h3>
      <p className="muted">Use the acquisition's actual Z, Y and X spacing. Unknown values are left blank; physical volume and length cannot be calculated without them.</p>
      {canRun ? <>
        <div className="row">
          {["Z", "Y", "X"].map((axis, index) => <label className="field" key={axis}>
            <span>{axis} (nm)</span>
            <input type="number" step="any" value={spacing[index]} disabled={busy}
              onChange={event => { setSpacing(values => values.map((value, i) => i === index ? event.target.value : value)); setSaved(false); }} />
          </label>)}
          <button type="button" className="secondary" disabled={busy || !dirty || !valid} onClick={() => void save()}>
            {busy ? "Saving voxel size…" : "Save voxel size"}
          </button>
        </div>
        {dirty && <p role="status">Unsaved voxel size. Save these metadata values before running measurements; annotation pixels are not changed.</p>}
        {saved && <p role="status">Voxel size saved.</p>}
      </> : <p>Z: {previous[0] || "Unknown"} · Y: {previous[1] || "Unknown"} · X: {previous[2] || "Unknown"} nm</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </section>
    <MitoMeasurements key={previous.join("-")} volume={current} canRun={canRun} runBlocked={dirty || busy} />
  </>;
}
