import { useEffect, useState } from "react";
import { fetchAnnotatorQuality } from "../api/quality";
import type { AnnotatorQuality } from "../types/quality";

/**
 * One annotator's measured quality, with a trend of their scored submissions.
 *
 * Renders nothing when the feature is disabled or the person has never been
 * measured. The rolling score is `null` — never `0.0` — until at least one
 * gold-standard submission has been scored, and this component must preserve
 * that distinction all the way to the screen: showing "0.00" for somebody who
 * has simply never been tested would misrepresent them.
 */
export default function AnnotatorQualitySection({
  username,
}: {
  username: string;
}) {
  const [data, setData] = useState<AnnotatorQuality | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let alive = true;
    fetchAnnotatorQuality(username)
      .then((result) => alive && setData(result))
      .catch(() => alive && setUnavailable(true));
    return () => {
      alive = false;
    };
  }, [username]);

  if (unavailable || !data) return null;
  if (data.score === null && data.history.length === 0) return null;

  const scored = data.history.filter(
    (entry): entry is typeof entry & { dice: number } => entry.dice !== null,
  );

  return (
    <section className="section-block">
      <h3>Measured quality</h3>
      <div className="summary-strip">
        <div className="summary-metric">
          <strong>{data.score === null ? "—" : data.score.toFixed(3)}</strong>
          <span>Rolling gold-standard Dice</span>
        </div>
        <div className="summary-metric">
          <strong>{scored.length}</strong>
          <span>Scored submissions</span>
        </div>
      </div>
      {data.score === null && (
        <p className="muted">
          Not measured yet — this person has no scored gold-standard
          submissions. That is not the same as scoring zero.
        </p>
      )}
      {scored.length > 1 && <Sparkline values={scored.map((entry) => entry.dice)} />}
    </section>
  );
}

/**
 * Dice over the recent scored submissions, oldest on the left.
 *
 * The y-axis is pinned to 0–1 rather than to the data range: Dice is already a
 * ratio, and auto-scaling it would turn a run of 0.94–0.96 into a dramatic
 * cliff that says nothing.
 */
function Sparkline({ values }: { values: number[] }) {
  const width = 240;
  const height = 44;
  const padding = 4;
  // The API returns newest first; a trend reads left-to-right in time.
  const series = [...values].reverse();
  const points = series.map((value, index) => {
    const x =
      padding +
      (series.length === 1
        ? (width - padding * 2) / 2
        : (index / (series.length - 1)) * (width - padding * 2));
    const y = padding + (1 - value) * (height - padding * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="chart-svg quality-sparkline"
      role="img"
      aria-label={`Dice trend over ${series.length} scored submissions, latest ${
        series[series.length - 1]?.toFixed(3) ?? "—"
      }`}
    >
      <line
        x1={padding} x2={width - padding}
        y1={padding} y2={padding}
        className="chart-grid"
      />
      <polyline points={points.join(" ")} className="chart-line" fill="none" />
      <circle
        cx={Number(points[points.length - 1].split(",")[0])}
        cy={Number(points[points.length - 1].split(",")[1])}
        r={3}
        className="quality-sparkline-head"
      />
    </svg>
  );
}
