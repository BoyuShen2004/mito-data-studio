import { useState } from "react";
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
  const cases = useAsync(() => listHardCases(), []);

  const rows = cases.data ?? [];
  const open = rows.filter((c) => c.status === "open");
  const resolved = rows.filter((c) => c.status === "resolved");

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
