import { Link } from "react-router-dom";
import { displayTaskLayerRange } from "../features/viewer/layerIndex";
import { setHardCaseStatus } from "../api/hardCases";
import type { HardCase } from "../types/hardCase";

/**
 * Hard cases, newest first — the shared list body for the `/hard-cases` inbox
 * and the per-project section. Reads like email: most recent receipt on top,
 * resolved ones visually settled but still there.
 *
 * Every permission shown here (`can_take_down`) comes from the API row, not
 * from re-deriving roles client-side.
 */
export default function HardCaseList({
  cases,
  showProject = true,
  onChanged,
  emptyText = "No hard cases yet.",
}: {
  cases: HardCase[];
  showProject?: boolean;
  onChanged?: () => void;
  emptyText?: string;
}) {
  if (cases.length === 0) {
    return (
      <p className="muted" style={{ marginBottom: 0 }}>
        {emptyText}
      </p>
    );
  }

  const takeDown = async (c: HardCase) => {
    const next = c.status === "open" ? "resolved" : "open";
    if (
      next === "resolved" &&
      !window.confirm(
        `Take down hard case #${c.id}? It stays readable for everyone on the project, but drops off the open list.`,
      )
    ) {
      return;
    }
    await setHardCaseStatus(c.id, next);
    onChanged?.();
  };

  return (
    <ul className="hard-case-list">
      {cases.map((c) => (
        <li
          key={c.id}
          className={`hard-case-row${c.status === "resolved" ? " hard-case-resolved" : ""}`}
        >
          <div className="row spread">
            <div>
              <Link to={`/hard-cases/${c.id}`}>
                <strong>Label #{c.label_id}</strong>
              </Link>
              {c.revoked && (
                <span className="muted" style={{ fontSize: "0.75rem" }}>
                  {" "}
                  · public link revoked
                </span>
              )}
              <div className="muted" style={{ fontSize: "0.78rem" }}>
                {showProject && c.project_title ? `${c.project_title} · ` : ""}
                {c.volume_name || "volume"} · z{displayTaskLayerRange(c.z_start, c.z_end)} · task #{c.task}
              </div>
              {c.note && (
                <div className="hard-case-note" title={c.note}>
                  {c.note}
                </div>
              )}
              <div className="muted" style={{ fontSize: "0.78rem" }}>
                by {c.created_by_username || "—"} ·{" "}
                {new Date(c.created_at).toLocaleString()}
                {c.status === "resolved" && c.resolved_by_username
                  ? ` · taken down by ${c.resolved_by_username}`
                  : ""}
              </div>
            </div>
            <div className="row">
              <Link to={`/hard-cases/${c.id}`}>
                <button type="button" className="secondary">
                  {c.can_annotate ? "Open" : "View"}
                </button>
              </Link>
              {c.can_take_down && (
                <button
                  type="button"
                  className="secondary"
                  onClick={() => void takeDown(c)}
                >
                  {c.status === "open" ? "Take down" : "Reopen"}
                </button>
              )}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
