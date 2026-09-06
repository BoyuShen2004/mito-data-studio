import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { fetchProjectInstanceSummary } from "../api/instanceAnnotations";
import { fetchProjectQuality } from "../api/quality";
import { CategoryBars, Metric } from "./charts/Charts";
import type { InstanceSummary } from "../types/instanceAnnotation";
import type { ProjectQuality } from "../types/quality";

/** A measured ratio, or an em-dash when the backend recorded no measurement. */
function ratio(value: number | null): string {
  return value === null || value === undefined ? "—" : value.toFixed(3);
}

export default function QualityPanel({ projectId }: { projectId: number }) {
  const [quality, setQuality] = useState<ProjectQuality | null>(null);
  const [instances, setInstances] = useState<InstanceSummary | null>(null);
  const [qualityOff, setQualityOff] = useState(false);
  const [instancesOff, setInstancesOff] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    // The two halves are independently flagged, so one being off must not
    // hide the other.
    const one = fetchProjectQuality(projectId)
      .then((data) => alive && setQuality(data))
      .catch((err) => {
        if (alive && err instanceof ApiError && err.status === 503) {
          setQualityOff(true);
        }
      });
    const two = fetchProjectInstanceSummary(projectId)
      .then((data) => alive && setInstances(data))
      .catch((err) => {
        if (alive && err instanceof ApiError && err.status === 503) {
          setInstancesOff(true);
        }
      });
    Promise.all([one, two]).finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [projectId]);

  if (loading) return <p className="muted">Loading…</p>;
  if (qualityOff && instancesOff) {
    return (
      <div className="empty-state">
        Quality metrics and instance annotation are not enabled on this
        deployment.
      </div>
    );
  }

  return (
    <>
      {instances && <InstanceSummarySection summary={instances} />}
      {quality && <ScoreSection quality={quality} />}
    </>
  );
}

function InstanceSummarySection({ summary }: { summary: InstanceSummary }) {
  return (
    <section className="section-block">
      <div className="section-heading">
        <h2>Instance annotation</h2>
        <p className="muted">
          What annotators recorded about individual mitochondria. Unclassified
          means nobody has judged the shape yet — it is not the same as “normal”.
        </p>
      </div>
      <div className="summary-strip">
        <Metric label="Instances annotated" value={summary.total_annotated} />
        <Metric
          label="Phenotype unclassified"
          value={summary.morphology_unclassified}
          hint="Carries a flag or a note, but no morphology"
        />
      </div>

      <h3>Morphology</h3>
      <CategoryBars
        rows={summary.vocabulary.morphology.map((entry) => ({
          label: entry.label,
          value: summary.morphology[entry.value] ?? 0,
        }))}
        emptyMessage="No morphology recorded yet."
      />

      <h3>Annotation issues</h3>
      <CategoryBars
        tone="warn"
        rows={summary.vocabulary.qa_flags.map((entry) => ({
          label: entry.label,
          value: summary.qa_flags[entry.value] ?? 0,
        }))}
        emptyMessage="Nothing flagged."
      />
    </section>
  );
}

function ScoreSection({ quality }: { quality: ProjectQuality }) {
  const gold = quality.by_kind.gold_standard;
  const reviewer = quality.by_kind.reviewer_agreement;

  return (
    <>
      <section className="section-block">
        <div className="section-heading">
          <h2>Measured agreement</h2>
          <p className="muted">
            A dash means the metric was never measured. It is deliberately not
            shown as zero — “not measured” and “scored badly” are different.
          </p>
        </div>
        <div className="summary-strip">
          <Metric
            label="Gold-standard scores"
            value={gold?.count ?? 0}
            hint="Submissions scored against a trusted reference"
          />
          <Metric
            label="Mean gold Dice"
            value={gold?.count ? ratio(gold.mean_dice) : null}
          />
          <Metric
            label="Reviewer-agreement scores"
            value={reviewer?.count ?? 0}
            hint="How much correction a reviewer applied"
          />
          <Metric
            label="Mean reviewer Dice"
            value={reviewer?.count ? ratio(reviewer.mean_dice) : null}
          />
        </div>

        {quality.gold_standard_volumes.length > 0 && (
          <>
            <h3>Gold-standard volumes</h3>
            <ul className="plain-list">
              {quality.gold_standard_volumes.map((volume) => (
                <li key={volume.id}>
                  <Link to={`/volumes/${volume.id}`}>{volume.name}</Link>
                  {volume.reference_submission_id === null && (
                    <span className="muted"> · no reference set</span>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      {quality.people.length > 0 && (
        <section className="section-block">
          <div className="section-heading">
            <h2>By annotator</h2>
            <p className="muted">
              Dice measures voxel overlap; instance F1, false merges and false
              splits measure whether individual mitochondria came out as
              separate objects — a perfect Dice can still hide every neighbour
              being fused.
            </p>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Annotator</th>
                  <th>Scored</th>
                  <th>Mean Dice</th>
                  <th>Mean instance F1</th>
                  <th>Mean false merges</th>
                  <th>Mean false splits</th>
                </tr>
              </thead>
              <tbody>
                {quality.people.map((person) => (
                  <tr key={person.user_id}>
                    <td className="cell-name">
                      <Link to={`/people/${person.username}`}>
                        {person.username}
                      </Link>
                    </td>
                    <td>{person.scored}</td>
                    <td>{ratio(person.mean_dice)}</td>
                    <td>{ratio(person.mean_instance_f1)}</td>
                    <td>
                      {person.false_merges === null
                        ? "—"
                        : person.false_merges.toFixed(1)}
                    </td>
                    <td>
                      {person.false_splits === null
                        ? "—"
                        : person.false_splits.toFixed(1)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
