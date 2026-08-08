export function formatRegionCoverage(value: number): string {
  const percent = Math.max(0, Math.min(1, value)) * 100;
  if (percent === 0) return "0%";
  if (percent < 0.05) return "<0.1%";
  if (percent < 1) return `${percent.toFixed(1)}%`;
  return `${Math.round(percent)}%`;
}

export default function RegionCoverage({
  hasMask,
  coverage,
}: {
  hasMask: boolean;
  coverage: number | null | undefined;
}) {
  if (!hasMask) return <span className="muted">—</span>;
  if (coverage == null) return <span className="muted">Unknown</span>;
  const empty = coverage === 0;
  return (
    <span
      className={`region-coverage${empty ? " region-coverage-empty" : ""}`}
      title={empty ? "The region mask is empty" : "Non-zero region-mask voxels"}
    >
      {formatRegionCoverage(coverage)}{empty ? " · empty" : ""}
    </span>
  );
}
