import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  getHardCase,
  setHardCaseRevoked,
  setHardCaseStatus,
} from "../api/hardCases";
import { useAsync } from "../hooks/useAsync";
import ViewerShell from "../components/ViewerShell";
import AnnotationCanvas, {
  type AxisControls,
} from "../features/viewer/AnnotationCanvas";
import AxisSelect from "../features/viewer/AxisSelect";
import RegionOnlyButton from "../features/viewer/RegionOnlyButton";
import Breadcrumb from "../components/Breadcrumb";
import { HardCaseDiscussion } from "../components/HardCaseNotesModal";
import StatusBadge from "../components/StatusBadge";
import { displayTaskLayerRange } from "../features/viewer/layerIndex";
import { categoryLabel } from "../features/viewer/hardCaseCategory";
import { relativeTime } from "../time";
import { hasViewCoordinates } from "../features/viewer/viewLocation";

/**
 * One hard case, opened by a project member at `/hard-cases/:id` — this
 * application's issue page.
 *
 * Same skeleton as the task page: title, `#id`, a state pill, a timeline with
 * its reply box at the end, and metadata in a sidebar. The canvas sits above
 * the discussion, full width, because for a hard case the picture *is* the
 * subject.
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
  const navigate = useNavigate();
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

  // Bare `/hard-cases/:id` (no z/y/x) should still open on the plane captured
  // at record time. `app_url` already carries those params when present.
  const needsViewRedirect = Boolean(
    hardCase &&
      hardCase.view_z != null &&
      hardCase.view_y != null &&
      hardCase.view_x != null &&
      !hasViewCoordinates(window.location.search),
  );

  useEffect(() => {
    if (!hardCase || !needsViewRedirect) return;
    navigate(hardCase.app_url, { replace: true });
  }, [needsViewRedirect, hardCase, navigate]);

  if (loading) return <p className="muted">Loading hard case…</p>;
  if (error) return <div className="error">{error}</div>;
  if (!hardCase) return null;
  if (needsViewRedirect) return <p className="muted">Opening recorded layer…</p>;

  const firstLine = hardCase.note.split("\n")[0]?.trim() ?? "";
  const title = firstLine || `Label #${hardCase.label_id}`;

  return (
    <div className="hard-case-page">
      <Breadcrumb
        items={[
          {
            label: hardCase.project_title || "Project",
            to: hardCase.project ? `/projects/${hardCase.project}` : undefined,
          },
          {
            label: "Cases",
            to: hardCase.project ? `/projects/${hardCase.project}?tab=cases` : undefined,
          },
          { label: `${title} #${hardCase.id}` },
        ]}
      />

      {/* Identity only — the controls that change this case are in the sidebar. */}
      <header className="task-header">
        <div className="row task-title-line">
          <h1>{title}</h1>
          <span className="task-number">#{hardCase.id}</span>
          <StatusBadge value={hardCase.status === "open" ? "in_review" : "completed"} />
          <span className="muted">{hardCase.status === "open" ? "open" : "taken down"}</span>
          {!hardCase.can_annotate && <span className="muted">· view only</span>}
        </div>
        <p className="muted">
          Label #{hardCase.label_id} on{" "}
          <Link to={`/volumes/${hardCase.volume}`}>{hardCase.volume_name || "volume"}</Link> · z
          {displayTaskLayerRange(hardCase.z_start, hardCase.z_end)} ·{" "}
          <Link to={`/tasks/${hardCase.task}`}>task #{hardCase.task}</Link> · raised{" "}
          {relativeTime(hardCase.created_at)} by {hardCase.created_by_username || "—"}
        </p>
      </header>

      <ViewerShell
        embedded
        topbar={
          axisControls ? (
            <>
              <span className="muted" style={{ fontSize: "0.78rem" }}>
                Soloed on label #{hardCase.label_id}
              </span>
              <span className="spacer" />
              <div className="editor-actions">
                <AxisSelect
                  id="topbar-hard-case-axis"
                  value={axisControls.axis}
                  onChange={axisControls.changeAxis}
                  disabled={axisControls.disabled}
                />
                <RegionOnlyButton controls={axisControls} />
              </div>
            </>
          ) : undefined
        }
      >
        <AnnotationCanvas
          taskId={hardCase.task}
          volumeId={hardCase.volume ?? 0}
          zStart={hardCase.z_start}
          zEnd={hardCase.z_end}
          mode={hardCase.can_annotate ? "annotate" : "view"}
          editable={hardCase.can_annotate}
          initialActiveId={hardCase.label_id}
          initialSoloId={hardCase.label_id}
          onAxisControls={onAxisControls}
        />
      </ViewerShell>

      <div className="hard-case-page-body">
        <div>
          <HardCaseDiscussion hardCase={hardCase} onChanged={reload} headings="h3" />
        </div>

        <aside className="task-sidebar" aria-label="Case details">
          <div className="sidebar-field">
            <div className="eyebrow">Category</div>
            <div className={hardCase.category ? "" : "muted"}>
              {categoryLabel(hardCase.category)}
            </div>
          </div>
          <div className="sidebar-field">
            <div className="eyebrow">Raised by</div>
            <div>{hardCase.created_by_username || "—"}</div>
          </div>
          {hardCase.status === "resolved" && (
            <div className="sidebar-field">
              <div className="eyebrow">Taken down by</div>
              <div>{hardCase.resolved_by_username || "—"}</div>
            </div>
          )}
          <div className="sidebar-field">
            <div className="eyebrow">Volume</div>
            <Link to={`/volumes/${hardCase.volume}`}>{hardCase.volume_name || "volume"}</Link>
          </div>
          <div className="sidebar-field">
            <div className="eyebrow">Task</div>
            <Link to={`/tasks/${hardCase.task}`}>#{hardCase.task}</Link>
          </div>

          <div className="sidebar-field">
            <div className="eyebrow">Public link</div>
            <div className="hard-case-actions">
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
                <button
                  type="button"
                  className="secondary"
                  onClick={() => void toggleRevoked()}
                  disabled={busy}
                  title="Kills only the public link — project members keep access."
                >
                  {hardCase.revoked ? "Restore link" : "Revoke link"}
                </button>
              )}
              {copyState === "failed" && <span className="error">Could not copy the link.</span>}
            </div>
          </div>

          {hardCase.can_take_down && (
            <div className="sidebar-field">
              <div className="eyebrow">State</div>
              <div className="hard-case-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => void toggleStatus()}
                  disabled={busy}
                >
                  {hardCase.status === "open" ? "Take down" : "Reopen"}
                </button>
              </div>
            </div>
          )}
          {notice && <p className="error" role="alert">{notice}</p>}
        </aside>
      </div>
    </div>
  );
}
