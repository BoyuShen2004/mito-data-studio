// New / To Proofread / Done tab bar. Reusable across dashboards.

import { LIFECYCLE_ORDER, lifecycleLabel, type Lifecycle } from "../../labels";
import type { LifecycleCounts } from "./api";

interface Props {
  active: Lifecycle | "all";
  counts?: LifecycleCounts;
  onChange: (value: Lifecycle | "all") => void;
}

export default function LifecycleTabs({ active, counts, onChange }: Props) {
  const total = counts
    ? LIFECYCLE_ORDER.reduce((s, k) => s + (counts[k] ?? 0), 0)
    : undefined;

  const tab = (value: Lifecycle | "all", label: string, count?: number) => (
    <button
      key={value}
      className={value === active ? "" : "secondary"}
      onClick={() => onChange(value)}
    >
      {label}
      {count !== undefined ? ` (${count})` : ""}
    </button>
  );

  return (
    <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
      {tab("all", "All", total)}
      {LIFECYCLE_ORDER.map((k) => tab(k, lifecycleLabel(k), counts?.[k]))}
    </div>
  );
}
