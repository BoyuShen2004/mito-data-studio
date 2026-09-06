import { useMemo, useState } from "react";
import {
  HARD_CASE_CATEGORIES,
  UNCATEGORISED_LABEL,
} from "../features/viewer/hardCaseCategory";
import { listHardCases } from "../api/hardCases";
import { useAsync } from "../hooks/useAsync";
import HardCaseList from "../components/HardCaseList";

/**
 * Hard Cases inbox — every case the signed-in user may see, across all their
 * projects, newest first (earlier receipts further down, like email).
 *
 * Visibility is project membership, decided server-side; this page never
 * filters by role. Resolved cases stay listed under a toggle rather than
 * disappearing: the record of what the team already worked through is the
 * point of the inbox.
 */
export default function HardCasesPage() {
  const [showResolved, setShowResolved] = useState(false);
  const [category, setCategory] = useState("");
  const cases = useAsync(() => listHardCases(), []);

  const rows = cases.data ?? [];
  // Filtered client-side: the inbox is already fully loaded, and a round trip
  // per chip click would make the counts flicker for no benefit.
  const visible = useMemo(
    () =>
      category === ""
        ? rows
        : rows.filter((c) =>
            category === "uncategorised" ? !c.category : c.category === category,
          ),
    [rows, category],
  );
  const open = visible.filter((c) => c.status === "open");
  const resolved = visible.filter((c) => c.status === "resolved");

  // Counts come from every row, not the filtered set, so a chip always says
  // how many it would show rather than how many survived the current filter.
  const counts = useMemo(() => {
    const tally = new Map<string, number>();
    for (const row of rows) {
      const key = row.category || "uncategorised";
      tally.set(key, (tally.get(key) ?? 0) + 1);
    }
    return tally;
  }, [rows]);

  return (
    <>
      <div className="row spread">
        <h1>Hard Cases</h1>
        <label className="row" style={{ gap: "0.4rem", alignItems: "center" }}>
          <input
            type="checkbox"
            checked={showResolved}
            onChange={(e) => setShowResolved(e.target.checked)}
          />
          <span className="muted">Show taken-down cases ({resolved.length})</span>
        </label>
      </div>

      <p className="muted">
        This is the project-membership inbox for labels your teammates flagged as hard. Anyone on the project can look;
        the person who recorded a case and the project’s managers can annotate
        it or take it down.
      </p>

      <div className="hard-case-filter-row" role="group" aria-label="Filter by category">
        <button
          type="button"
          aria-pressed={category === ""}
          className={`hard-case-filter-chip${category === "" ? " hard-case-filter-chip-active" : ""}`}
          onClick={() => setCategory("")}
        >
          All ({rows.length})
        </button>
        {HARD_CASE_CATEGORIES.map((entry) => {
          const n = counts.get(entry.value) ?? 0;
          return (
            <button
              key={entry.value}
              type="button"
              aria-pressed={category === entry.value}
              title={entry.hint}
              disabled={n === 0}
              className={`hard-case-filter-chip${category === entry.value ? " hard-case-filter-chip-active" : ""}`}
              onClick={() => setCategory(entry.value)}
            >
              {entry.label} ({n})
            </button>
          );
        })}
        {(counts.get("uncategorised") ?? 0) > 0 && (
          <button
            type="button"
            aria-pressed={category === "uncategorised"}
            className={`hard-case-filter-chip${category === "uncategorised" ? " hard-case-filter-chip-active" : ""}`}
            onClick={() => setCategory("uncategorised")}
          >
            {UNCATEGORISED_LABEL} ({counts.get("uncategorised")})
          </button>
        )}
      </div>

      {cases.error && <div className="error">{cases.error}</div>}

      <div className="card">
        {cases.loading ? (
          <p className="muted">Loading…</p>
        ) : (
          <HardCaseList
            cases={open}
            onChanged={cases.reload}
            emptyText="Nothing open. Flag one from Annotate with “Record hard case”."
          />
        )}
      </div>

      {showResolved && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Taken down ({resolved.length})</h3>
          <HardCaseList
            cases={resolved}
            onChanged={cases.reload}
            emptyText="Nothing has been taken down yet."
          />
        </div>
      )}
    </>
  );
}
