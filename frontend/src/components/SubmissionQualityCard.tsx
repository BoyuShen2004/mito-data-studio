import { useEffect, useState } from "react";
import { fetchSubmissionQuality } from "../api/quality";
import type { QualityScore } from "../types/quality";

/** A measured ratio, or an em-dash when nothing was measured. */
function ratio(value: number | null): string {
  return value === null || value === undefined ? "—" : value.toFixed(3);
}

function count(value: number | null): string {
  return value === null || value === undefined ? "—" : String(value);
}

const KIND_LABELS: Record<string, string> = {
  gold_standard: "Scored against the gold-standard reference",
  reviewer_agreement: "Annotator versus the approved label",
};

/**
 * Measured overlap for one submission, shown to a reviewer.
 *
 * Renders nothing when there is no score — which is the common case, since a
 * score only exists for a gold-standard volume or after a reviewer corrected
 * the work. An empty card claiming "0.000 Dice" would be a false accusation,
 * so absence is shown as absence.
 */
export default function SubmissionQualityCard({
  submissionId,
}: {
  submissionId: number;
}) {
  const [scores, setScores] = useState<QualityScore[] | null>(null);

  useEffect(() => {
    let alive = true;
    // `fetchSubmissionQuality` swallows a 503 into an empty list, so a
    // deployment without quality metrics simply renders nothing here.
    fetchSubmissionQuality(submissionId).then((result) => {
      if (alive) setScores(result);
    });
    return () => {
      alive = false;
    };
  }, [submissionId]);

  if (!scores || scores.length === 0) return null;

  return (
    <div className="card">
      <h3>Measured quality</h3>
      {scores.map((score) => (
        <div key={score.id} className="quality-score-block">
          <p className="muted">{KIND_LABELS[score.kind] ?? score.kind}</p>
          <div className="summary-strip">
            <div className="summary-metric">
              <strong>{ratio(score.dice)}</strong>
              <span>Dice</span>
            </div>
            <div className="summary-metric">
              <strong>{ratio(score.iou)}</strong>
              <span>IoU</span>
            </div>
            <div className="summary-metric">
              <strong>{ratio(score.instance_f1)}</strong>
              <span>Instance F1</span>
            </div>
            <div
              className={`summary-metric${score.false_merges ? " summary-metric-warn" : ""}`}
            >
              <strong>{count(score.false_merges)}</strong>
              <span>False merges</span>
            </div>
            <div
              className={`summary-metric${score.false_splits ? " summary-metric-warn" : ""}`}
            >
              <strong>{count(score.false_splits)}</strong>
              <span>False splits</span>
            </div>
          </div>
          <p className="muted">
            Dice counts voxels; instance F1, merges and splits count whether
            individual mitochondria came out as separate objects. High Dice with
            merges present means the shape is right but the objects are fused.
          </p>
        </div>
      ))}
    </div>
  );
}
