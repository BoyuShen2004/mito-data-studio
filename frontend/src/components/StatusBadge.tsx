const COLORS: Record<string, string> = {
  // task status
  unassigned: "badge-gray",
  assigned: "badge-blue",
  in_progress: "badge-blue",
  submitted: "badge-amber",
  approved: "badge-green",
  rejected: "badge-red",
  revision_requested: "badge-amber",
  cancelled: "badge-red",
  // qc
  not_run: "badge-gray",
  passed: "badge-green",
  warning: "badge-amber",
  failed: "badge-red",
  // project status
  draft: "badge-gray",
  active: "badge-blue",
  in_annotation: "badge-blue",
  in_review: "badge-amber",
  completed: "badge-green",
  delivered: "badge-green",
  // label types
  none: "badge-gray",
  prediction: "badge-blue",
  proofread: "badge-green",
  partial: "badge-amber",
  // read-only streaming derivative
  ready: "badge-green",
  building: "badge-blue",
  not_built: "badge-gray",
};

export default function StatusBadge({ value }: { value: string }) {
  const cls = COLORS[value] ?? "badge-gray";
  const label = value.replace(/_/g, " ");
  return <span className={`badge ${cls}`}>{label}</span>;
}
