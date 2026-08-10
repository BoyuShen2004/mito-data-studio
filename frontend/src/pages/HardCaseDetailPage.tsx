import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getHardCase,
  setHardCaseRevoked,
  setHardCaseStatus,
} from "../api/hardCases";
import { useAsync } from "../hooks/useAsync";
import ViewerShell, { ViewerShellMessage } from "../components/ViewerShell";
import AnnotationCanvas, {
  type AxisControls,
} from "../features/viewer/AnnotationCanvas";
import AxisSelect from "../features/viewer/AxisSelect";
import RegionOnlyButton from "../features/viewer/RegionOnlyButton";

/**
 * One hard case, opened by a project member at `/hard-cases/:id`.
 *
 * Same `AnnotationCanvas` as task View / Annotate and the public share page —
 * there is no second editor. `editable` is the server's `can_annotate` (the
 * creator or a manager, *and* only while they still have live edit access to
 * the underlying task), so the canvas can never offer a paint tool whose
 * write would 403. Everyone else in the audience gets the same canvas,
 * View-only, soloed on the flagged label.
 *
 * Reads and writes both go through the ordinary authed task endpoints — the
 * public token path is only for people without an account.
 */
export default function HardCaseDetailPage() {
  const { id } = useParams();
  const caseId = Number(id);
  const { data: hardCase, loading, error, reload } = useAsync(
    () => getHardCase(caseId),
    [caseId],
  );
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [axisControls, setAxisControls] = useState<AxisControls | null>(null);
  const onAxisControls = useCallback((c: AxisControls | null) => {
    setAxisControls(c);
  }, []);

  const publicUrl = hardCase ? window.location.origin + hardCase.url : "";

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(publicUrl);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  };

  const toggleStatus = async () => {
    if (!hardCase) return;
    const next = hardCase.status === "open" ? "resolved" : "open";
    if (
      next === "resolved" &&
      !window.confirm(
        "Take this hard case down? It stays readable for everyone on the project, but drops off the open list.",
      )
    ) {
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      await setHardCaseStatus(hardCase.id, next);
      reload();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not update the case.");
    } finally {
      setBusy(false);
    }
  };

  const toggleRevoked = async () => {
    if (!hardCase) return;
    setBusy(true);
    setNotice(null);
    try {
      await setHardCaseRevoked(hardCase.id, !hardCase.revoked);
      reload();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not update the link.");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <ViewerShellMessage>Loading hard case…</ViewerShellMessage>;
  if (error) return <ViewerShellMessage tone="error">{error}</ViewerShellMessage>;
  if (!hardCase) return null;

  return (
    <ViewerShell
      topbar={
        <>
          <h1>Hard case · label #{hardCase.label_id}</h1>
          {!hardCase.can_annotate && (
            <span className="muted" style={{ fontSize: "0.78rem" }}>
              View only
            </span>
          )}
          <span className="muted" style={{ fontSize: "0.78rem" }}>
            {hardCase.project_title} ·{" "}
            {hardCase.volume_name} · by{" "}
            {hardCase.created_by_username || "—"} ·{" "}
            {new Date(hardCase.created_at).toLocaleDateString()}
          </span>
          {hardCase.note && (
            <span className="hard-case-detail-note" title={hardCase.note}>
              Note: {hardCase.note}
            </span>
          )}
          <span className="spacer" />
          {notice && <span className="error">{notice}</span>}
          <div className="editor-actions">
            {axisControls && (
              <>
                <AxisSelect
                  id="topbar-hard-case-axis"
                  value={axisControls.axis}
                  onChange={axisControls.changeAxis}
                  disabled={axisControls.disabled}
                />
                <RegionOnlyButton controls={axisControls} />
              </>
            )}
            <button
              type="button"
              className="secondary"
              onClick={() => void copyLink()}
              disabled={hardCase.revoked}
              title={
                hardCase.revoked
                  ? "The public link for this case was revoked."
                  : "Copy the public, no-account read-only link."
              }
            >
              {copyState === "copied" ? "Copied" : "Copy link"}
            </button>
            {hardCase.can_take_down && (
              <>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => void toggleRevoked()}
                  disabled={busy}
                  title="Kills only the public link — project members keep access."
                >
                  {hardCase.revoked ? "Restore link" : "Revoke link"}
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => void toggleStatus()}
                  disabled={busy}
                >
                  {hardCase.status === "open" ? "Take down" : "Reopen"}
                </button>
              </>
            )}
            <Link to="/hard-cases">
              <button type="button" className="secondary">
                All cases
              </button>
            </Link>
          </div>
        </>
      }
    >
      <AnnotationCanvas
        taskId={hardCase.task}
        volumeId={hardCase.volume ?? 0}
        zStart={hardCase.z_start}
        zEnd={hardCase.z_end}
        editable={hardCase.can_annotate}
        initialActiveId={hardCase.label_id}
        initialSoloId={hardCase.label_id}
        onAxisControls={onAxisControls}
      />
    </ViewerShell>
  );
}
