import { useId, useState } from "react";

/**
 * Hand-authored inline SVG charts.
 *
 * No charting dependency: the app ships `three` for the 3D preview and nothing
 * else, and one delivery tab does not justify a library. Every chart here uses
 * the app's own CSS tokens (`--accent`, `--warn`, `--muted`, `--border`) so it
 * inherits the product's single light theme rather than importing a second
 * palette that would drift from it.
 *
 * Deliberately **no categorical colour scale anywhere**. Each chart shows one
 * measure, so identity comes from the axis labels and hue carries magnitude or
 * status only. Where a second line is needed (burndown's ideal) it is drawn as
 * a dashed grey *reference*, not as a peer series — so the pair is
 * distinguishable without relying on colour, which is also what keeps it
 * readable for a colour-blind reader and in print.
 */

interface Tooltip {
  x: number;
  y: number;
  text: string;
}

function TooltipLayer({ tip, width }: { tip: Tooltip | null; width: number }) {
  if (!tip) return null;
  // Keep the label inside the plot rather than letting it clip at the edges.
  const anchor = tip.x < 60 ? "start" : tip.x > width - 60 ? "end" : "middle";
  return (
    <text
      className="chart-tooltip"
      x={tip.x}
      y={Math.max(12, tip.y - 8)}
      textAnchor={anchor}
    >
      {tip.text}
    </text>
  );
}

/** Vertical bars over a date range — throughput. One series, so no legend. */
export function ThroughputChart({
  points,
  height = 140,
}: {
  points: { date: string; approved: number }[];
  height?: number;
}) {
  const [tip, setTip] = useState<Tooltip | null>(null);
  const width = 640;
  const padding = { top: 14, right: 8, bottom: 22, left: 30 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  if (points.length === 0) return <p className="muted">No data yet.</p>;

  const max = Math.max(1, ...points.map((point) => point.approved));
  // A 2px gap between adjacent bars, per the mark spec.
  const step = plotWidth / points.length;
  const barWidth = Math.max(1, step - 2);

  return (
    <figure className="chart-figure">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="chart-svg"
        role="img"
        aria-label={`Tasks approved per day, peak ${max}`}
        onMouseLeave={() => setTip(null)}
      >
        {/* Recessive baseline and one reference gridline at the peak. */}
        <line
          x1={padding.left} x2={width - padding.right}
          y1={padding.top} y2={padding.top}
          className="chart-grid"
        />
        <text x={4} y={padding.top + 4} className="chart-axis-label">{max}</text>
        <line
          x1={padding.left} x2={width - padding.right}
          y1={height - padding.bottom} y2={height - padding.bottom}
          className="chart-axis"
        />
        {points.map((point, index) => {
          const barHeight = (point.approved / max) * plotHeight;
          const x = padding.left + index * step + 1;
          const y = height - padding.bottom - barHeight;
          return (
            <rect
              key={point.date}
              x={x}
              y={point.approved === 0 ? height - padding.bottom - 1 : y}
              width={barWidth}
              height={point.approved === 0 ? 1 : barHeight}
              // Rounded data-end, anchored to the baseline.
              rx={2}
              className={point.approved === 0 ? "chart-bar-empty" : "chart-bar"}
              onMouseEnter={() =>
                setTip({
                  x: x + barWidth / 2,
                  y,
                  text: `${point.date}: ${point.approved} approved`,
                })
              }
            />
          );
        })}
        {/* Only the endpoints are labelled — never a number on every bar. */}
        <text x={padding.left} y={height - 6} className="chart-axis-label">
          {points[0].date.slice(5)}
        </text>
        <text
          x={width - padding.right}
          y={height - 6}
          textAnchor="end"
          className="chart-axis-label"
        >
          {points[points.length - 1].date.slice(5)}
        </text>
        <TooltipLayer tip={tip} width={width} />
      </svg>
    </figure>
  );
}

/** Remaining work against a straight reference descent. */
export function BurndownChart({
  actual,
  ideal,
  height = 160,
}: {
  actual: { date: string; remaining: number }[];
  ideal: { date: string; remaining: number }[];
  height?: number;
}) {
  const [tip, setTip] = useState<Tooltip | null>(null);
  const gradientId = useId();
  const width = 640;
  const padding = { top: 16, right: 46, bottom: 22, left: 34 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  if (actual.length === 0) return <p className="muted">No data yet.</p>;

  const max = Math.max(
    1,
    ...actual.map((point) => point.remaining),
    ...ideal.map((point) => point.remaining),
  );
  const xAt = (index: number, total: number) =>
    padding.left + (total <= 1 ? plotWidth / 2 : (index / (total - 1)) * plotWidth);
  const yAt = (value: number) =>
    padding.top + plotHeight - (value / max) * plotHeight;
  const path = (series: { remaining: number }[]) =>
    series
      .map(
        (point, index) =>
          `${index === 0 ? "M" : "L"}${xAt(index, series.length).toFixed(1)},${yAt(
            point.remaining,
          ).toFixed(1)}`,
      )
      .join(" ");

  const last = actual[actual.length - 1];

  return (
    <figure className="chart-figure">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="chart-svg"
        role="img"
        aria-label={`Remaining work, now ${last.remaining} of ${max}`}
        onMouseLeave={() => setTip(null)}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" className="chart-area-top" />
            <stop offset="100%" className="chart-area-bottom" />
          </linearGradient>
        </defs>

        <line
          x1={padding.left} x2={width - padding.right}
          y1={padding.top} y2={padding.top}
          className="chart-grid"
        />
        <text x={4} y={padding.top + 4} className="chart-axis-label">{max}</text>
        <line
          x1={padding.left} x2={width - padding.right}
          y1={height - padding.bottom} y2={height - padding.bottom}
          className="chart-axis"
        />

        {/* Reference first, so the real series sits on top of it. Dashed and
            grey: it is an annotation, not a second measured series, and the
            dash carries the distinction without relying on colour. */}
        <path d={path(ideal)} className="chart-reference-line" fill="none" />
        <path
          d={`${path(actual)} L${xAt(actual.length - 1, actual.length)},${
            height - padding.bottom
          } L${xAt(0, actual.length)},${height - padding.bottom} Z`}
          fill={`url(#${gradientId})`}
          stroke="none"
        />
        <path d={path(actual)} className="chart-line" fill="none" />

        {actual.map((point, index) => (
          <circle
            key={point.date}
            cx={xAt(index, actual.length)}
            cy={yAt(point.remaining)}
            r={8}
            fill="transparent"
            onMouseEnter={() =>
              setTip({
                x: xAt(index, actual.length),
                y: yAt(point.remaining),
                text: `${point.date}: ${point.remaining} left`,
              })
            }
          />
        ))}

        {/* Direct labels rather than a legend box: two marks, both named. */}
        <text
          x={width - padding.right + 4}
          y={yAt(last.remaining) + 4}
          className="chart-direct-label"
        >
          Actual
        </text>
        <text
          x={width - padding.right + 4}
          y={yAt(ideal[ideal.length - 1]?.remaining ?? 0) + 4}
          className="chart-direct-label chart-direct-label-muted"
        >
          Ideal
        </text>
        <TooltipLayer tip={tip} width={width} />
      </svg>
    </figure>
  );
}

/**
 * Category magnitude as horizontal bars, sorted by size.
 *
 * Identity comes from the row labels, so one hue carries magnitude and no
 * categorical palette is needed however many categories there are.
 */
export function CategoryBars({
  rows,
  tone = "accent",
  emptyMessage = "Nothing recorded yet.",
}: {
  rows: { label: string; value: number; hint?: string }[];
  tone?: "accent" | "warn";
  emptyMessage?: string;
}) {
  const shown = rows.filter((row) => row.value > 0);
  if (shown.length === 0) return <p className="muted">{emptyMessage}</p>;

  const max = Math.max(...shown.map((row) => row.value));
  const sorted = [...shown].sort((a, b) => b.value - a.value);

  return (
    <ul className={`category-bars category-bars-${tone}`}>
      {sorted.map((row) => (
        <li key={row.label} title={row.hint}>
          <span className="category-bars-label">{row.label}</span>
          <span className="category-bars-track">
            <span
              className="category-bars-fill"
              style={{ width: `${Math.max(2, (row.value / max) * 100)}%` }}
            />
          </span>
          <span className="category-bars-value">{row.value}</span>
        </li>
      ))}
    </ul>
  );
}

/** A single measured value, or an explicit em-dash when never measured. */
export function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  /** `null` renders "—". It must never render as 0: "not measured" and
   * "measured as zero" are different claims. */
  value: number | string | null;
  hint?: string;
}) {
  return (
    <div className="chart-metric" title={hint}>
      <strong>{value === null ? "—" : value}</strong>
      <span>{label}</span>
    </div>
  );
}
