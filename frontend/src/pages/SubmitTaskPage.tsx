import { useState } from "react";
import { displayTaskLayerRange } from "../features/viewer/layerIndex";
import { Link, useParams } from "react-router-dom";
import { getTask } from "../api/tasks";
import { submitTask } from "../api/submissions";
import { useAsync } from "../hooks/useAsync";
import FileUpload from "../components/FileUpload";
import StatusBadge from "../components/StatusBadge";
import { offlineSubmitLabel } from "../labels";

export default function SubmitTaskPage() {
  const { id } = useParams();
  const taskId = Number(id);
  const { data: t, loading } = useAsync(() => getTask(taskId), [taskId]);

  const [file, setFile] = useState<File | null>(null);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) {
      window.alert("Please choose a label file.");
      return;
    }
    setBusy(true);
    try {
      const form = new FormData();
      form.append("label_file", file);
      form.append("notes", notes);
      const sub = await submitTask(taskId, form);
      // No timeout-then-navigate: a page that can show its own result should.
      // The task page is one deliberate click away, with the new round already
      // in its timeline.
      setResult(`Submitted. QC: ${sub.qc_status}.`);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Submission failed");
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
        {result ? (
          <>
            <p>{result}</p>
            <div className="row">
              <Link to={`/tasks/${taskId}`}>
                <button type="button">Back to task #{taskId}</button>
              </Link>
            </div>
          </>
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
