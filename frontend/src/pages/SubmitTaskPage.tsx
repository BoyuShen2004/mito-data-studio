import { useState } from "react";
import { displayTaskLayerRange } from "../features/viewer/layerIndex";
import { useNavigate, useParams } from "react-router-dom";
import { getTask } from "../api/tasks";
import { submitTask } from "../api/submissions";
import { useAsync } from "../hooks/useAsync";
import FileUpload from "../components/FileUpload";
import StatusBadge from "../components/StatusBadge";
import { useAuth } from "../auth/AuthContext";
import { offlineSubmitLabel } from "../components/TaskDetailsCards";

export default function SubmitTaskPage() {
  const { id } = useParams();
  const taskId = Number(id);
  const navigate = useNavigate();
  const {isManager} = useAuth();
  const { data: t, loading } = useAsync(() => getTask(taskId), [taskId]);

  const [file, setFile] = useState<File | null>(null);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) {
      setError("Please choose a label file.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("label_file", file);
      form.append("notes", notes);
      const sub = await submitTask(taskId, form);
      setResult(`Submitted. QC: ${sub.qc_status}.`);
      setTimeout(() => navigate(isManager && t ? `/volumes/${t.volume}` : `/tasks/${taskId}`), 1200);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submission failed");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <p className="muted">Loading…</p>;

  return (
    <>
      <h1>Offline annotation upload · task #{taskId}</h1>
      {t && (
        <p className="muted">
          {t.volume_name} · z {displayTaskLayerRange(t.z_start, t.z_end)} ·{" "}
          <StatusBadge value={t.status} />
        </p>
      )}
      <div className="card">
        {error && <div className="error">{error}</div>}
        {result ? (
          <p>{result}</p>
        ) : (
          <form onSubmit={submit}>
            <FileUpload
              label="Completed label file"
              accept=".tif,.tiff,.h5,.hdf5,.zarr,.npy,.nii,.nii.gz"
              onChange={setFile}
            />
            <label className="field">
              <span>Notes (optional)</span>
              <textarea
                rows={3}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </label>
            <button type="submit" disabled={busy}>
              {busy ? "Uploading…" : t ? offlineSubmitLabel(t) : "Submit completed label"}
            </button>
          </form>
        )}
      </div>
    </>
  );
}
