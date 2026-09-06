import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import {
  fetchApprovedSubmissions,
  setGoldStandard,
  type ApprovedSubmission,
} from "../api/quality";

/**
 * Marks one volume as a known test and picks the approved submission its
 * annotations are scored against.
 *
 * Manager-only, and hidden entirely for anyone else — including from the
 * annotators who will be assigned it. That is the point of a gold standard:
 * somebody who knows they are being measured is measuring their attention,
 * not their ordinary working accuracy.
 *
 * Renders nothing when quality metrics are disabled, so the card only appears
 * where it can actually do something.
 */
export default function GoldStandardCard({
  volumeId,
  isGoldStandard,
  referenceSubmission,
  isManager,
  onChange,
}: {
  volumeId: number;
  isGoldStandard: boolean;
  referenceSubmission: number | null;
  isManager: boolean;
  onChange: () => void;
}) {
  const [candidates, setCandidates] = useState<ApprovedSubmission[] | null>(null);
  const [available, setAvailable] = useState(true);
  const [selected, setSelected] = useState<string>(
    referenceSubmission ? String(referenceSubmission) : "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!isManager) return;
    let alive = true;
    fetchApprovedSubmissions(volumeId)
      .then((rows) => alive && setCandidates(rows))
      .catch((err) => {
        if (!alive) return;
        // 503 = the deployment has not enabled quality metrics, which is a
        // normal configuration rather than a failure worth reporting.
        if (err instanceof ApiError && err.status === 503) setAvailable(false);
        else setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, [volumeId, isManager]);

  if (!isManager || !available) return null;

  const save = async (enabled: boolean) => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await setGoldStandard(volumeId, {
        is_gold_standard: enabled,
        reference_submission: selected ? Number(selected) : null,
      });
      setSaved(true);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  };

  const noCandidates = candidates !== null && candidates.length === 0;

  return (
    <section className="card gold-standard-card" aria-label="Gold standard">
      <div className="row spread">
        <h3>Gold standard</h3>
        {isGoldStandard && (
          <span className="milestone-chip milestone-chip-gold">Active</span>
        )}
      </div>
      <p className="muted">
        Score every submission on this volume against a trusted answer. The
        annotator is not told — a known test measures attention, not ordinary
        accuracy.
      </p>

      {error && <div className="error">{error}</div>}
      {saved && !error && <div className="success">Saved.</div>}

      {candidates === null ? (
        <p className="muted">Loading approved submissions…</p>
      ) : noCandidates ? (
        <div className="empty-state">
          No approved submission on this volume yet. The reference has to be
          work that already passed review, so approve one first.
        </div>
      ) : (
        <>
          <label className="field">
            <span>Reference submission</span>
            <select
              value={selected}
              disabled={busy}
              onChange={(event) => setSelected(event.target.value)}
            >
              <option value="">— none —</option>
              {candidates.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  #{candidate.id} · {candidate.annotator ?? "unknown"} ·{" "}
                  {new Date(candidate.submitted_at).toLocaleDateString()}
                </option>
              ))}
            </select>
          </label>

          <div className="row">
            <button
              type="button"
              disabled={busy || !selected}
              onClick={() => save(true)}
              title={
                selected
                  ? undefined
                  : "Pick the approved submission to score against first"
              }
            >
              {busy ? "Saving…" : isGoldStandard ? "Update" : "Mark as gold standard"}
            </button>
            {isGoldStandard && (
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => save(false)}
              >
                Stop scoring
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
