import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  createMilestone,
  deleteMilestone,
  fetchMilestoneDetail,
  fetchMilestones,
  fetchProjectDelivery,
  updateMilestone,
} from "../api/milestones";
import { BurndownChart, CategoryBars, Metric, ThroughputChart } from "./charts/Charts";
import type { Milestone, ProjectDelivery } from "../types/milestone";

function formatDuration(seconds: number | null): string {
  // `null` is "unknowable" (a legacy-exempt volume), not zero. It must render
  // as an em-dash so nobody reads unmeasured history as no effort.
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export default function DeliveryPanel({ projectId }: { projectId: number }) {
  const [milestones, setMilestones] = useState<Milestone[]>([]);
  const [canEdit, setCanEdit] = useState(false);
  const [delivery, setDelivery] = useState<ProjectDelivery | null>(null);
  const [disabled, setDisabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([fetchMilestones(projectId), fetchProjectDelivery(projectId)])
      .then(([milestonePage, deliveryData]) => {
        setMilestones(milestonePage.results);
        setCanEdit(milestonePage.can_edit);
        setDelivery(deliveryData);
        setDisabled(false);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 503) setDisabled(true);
        else setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  }, [projectId]);

  useEffect(load, [load]);

  if (disabled) {
    return (
      <div className="empty-state">
        Milestones and delivery analytics are not enabled on this deployment.
      </div>
    );
  }
  if (loading) return <p className="muted">Loading…</p>;
  if (error) return <div className="error">{error}</div>;

  return (
    <>
      <MilestoneSection
        projectId={projectId}
        milestones={milestones}
        canEdit={canEdit}
        onChange={load}
      />
      {delivery && <AnalyticsSection delivery={delivery} />}
    </>
  );
}

function MilestoneSection({
  projectId,
  milestones,
  canEdit,
  onChange,
}: {
  projectId: number;
  milestones: Milestone[];
  canEdit: boolean;
  onChange: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [dueOn, setDueOn] = useState("");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await createMilestone(projectId, {
        name,
        due_on: dueOn,
        target_value: Number(target) || 0,
      });
      setName("");
      setDueOn("");
      setTarget("");
      setAdding(false);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the milestone.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="section-block">
      <div className="section-heading row spread">
        <div>
          <h2>Milestones</h2>
          <p className="muted">
            Dated targets inside this project. Progress is recomputed from the
            tasks on every load, never stored.
          </p>
        </div>
        {canEdit && (
          <button type="button" className="secondary" onClick={() => setAdding((v) => !v)}>
            {adding ? "Cancel" : "+ Milestone"}
          </button>
        )}
      </div>

      {adding && (
        <div className="milestone-form row">
          <label className="field">
            <span>Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>Due</span>
            <input type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} />
          </label>
          <label className="field">
            <span>Tasks approved</span>
            <input
              type="number"
              min={0}
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            />
          </label>
          <button type="button" disabled={busy || !name || !dueOn} onClick={submit}>
            {busy ? "Saving…" : "Add"}
          </button>
        </div>
      )}
      {error && <div className="error">{error}</div>}

      {milestones.length === 0 ? (
        <div className="empty-state">No milestones yet.</div>
      ) : (
        <ul className="milestone-list">
          {milestones.map((milestone) => (
            <MilestoneRow
              key={milestone.id}
              milestone={milestone}
              canEdit={canEdit}
              onChange={onChange}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function MilestoneRow({
  milestone,
  canEdit,
  onChange,
}: {
  milestone: Milestone;
  canEdit: boolean;
  onChange: () => void;
}) {
  const { progress } = milestone;
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(milestone.name);
  const [dueOn, setDueOn] = useState(milestone.due_on);
  const [target, setTarget] = useState(String(milestone.target_value));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const beginEdit = () => {
    // Re-seed from the milestone each time, so an abandoned edit does not
    // reappear the next time the row is opened.
    setName(milestone.name);
    setDueOn(milestone.due_on);
    setTarget(String(milestone.target_value));
    setError(null);
    setEditing(true);
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await updateMilestone(milestone.id, {
        name,
        due_on: dueOn,
        target_value: Number(target) || 0,
      });
      setEditing(false);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    // A milestone carries a manager's schedule and its volume scope; deleting
    // it on a single misplaced click is the kind of loss that is annoying to
    // reconstruct, so it asks first.
    const confirmed = window.confirm(
      `Remove the milestone “${milestone.name}”?\n\n` +
        "Its target and volume scope are deleted. Task progress itself is " +
        "untouched.",
    );
    if (!confirmed) return;
    setBusy(true);
    setError(null);
    try {
      await deleteMilestone(milestone.id);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove.");
      setBusy(false);
    }
  };

  return (
    <li
      className={`milestone-row${progress.overdue ? " milestone-row-overdue" : ""}`}
    >
      {editing ? (
        <div className="milestone-form row">
          <label className="field">
            <span>Name</span>
            <input
              value={name}
              disabled={busy}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="field">
            <span>Due</span>
            <input
              type="date"
              value={dueOn}
              disabled={busy}
              onChange={(event) => setDueOn(event.target.value)}
            />
          </label>
          <label className="field">
            <span>Target</span>
            <input
              type="number"
              min={0}
              value={target}
              disabled={busy}
              onChange={(event) => setTarget(event.target.value)}
            />
          </label>
          <button type="button" disabled={busy || !name || !dueOn} onClick={save}>
            {busy ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={() => setEditing(false)}
          >
            Cancel
          </button>
        </div>
      ) : (
        <div className="milestone-head row spread">
          <div>
            <strong>{milestone.name}</strong>
            <span className="muted"> · due {progress.due_on}</span>
            {progress.overdue && (
              <span className="milestone-chip milestone-chip-overdue">
                Overdue
              </span>
            )}
          </div>
          <div className="row">
            <span className="muted">
              {progress.achieved} / {progress.target_value}
            </span>
            {canEdit && (
              <>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={beginEdit}
                >
                  Edit
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={remove}
                >
                  Remove
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {error && <div className="error">{error}</div>}

      <div className="milestone-track">
        <div
          className="milestone-fill"
          style={{ width: `${progress.percent_complete ?? 0}%` }}
        />
      </div>
      <p className="muted">
        {progress.percent_complete === null
          ? "No target set."
          : `${progress.percent_complete}% complete · ${progress.remaining} left`}
        {progress.scoped_volumes > 0 &&
          ` · scoped to ${progress.scoped_volumes} volume(s)`}
      </p>
      {progress.target_value > 0 && (
        <MilestoneBurndown milestoneId={milestone.id} />
      )}
    </li>
  );
}

function MilestoneBurndown({ milestoneId }: { milestoneId: number }) {
  const [data, setData] = useState<{
    actual: { date: string; remaining: number }[];
    ideal: { date: string; remaining: number }[];
  } | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open || data) return;
    let alive = true;
    fetchMilestoneDetail(milestoneId)
      .then((response) => alive && setData(response.burndown))
      // An empty series renders the chart's own "No data yet." rather than an
      // error banner over a panel the user only expanded out of curiosity.
      .catch(() => alive && setData({ actual: [], ideal: [] }));
    return () => {
      alive = false;
    };
  }, [open, data, milestoneId]);

  return (
    <div className="milestone-burndown">
      <button type="button" className="link-button" onClick={() => setOpen((v) => !v)}>
        {open ? "Hide burndown" : "Show burndown"}
      </button>
      {open && data && <BurndownChart actual={data.actual} ideal={data.ideal} />}
    </div>
  );
}

function AnalyticsSection({ delivery }: { delivery: ProjectDelivery }) {
  const { throughput, productivity, attention } = delivery;
  const totalApproved = throughput.points.reduce(
    (sum, point) => sum + point.approved,
    0,
  );

  return (
    <>
      <section className="section-block">
        <div className="section-heading">
          <h2>Throughput</h2>
          <p className="muted">
            Tasks approved per day, {throughput.start} to {throughput.end}.
          </p>
        </div>
        <div className="summary-strip">
          <Metric label="Approved in window" value={totalApproved} />
          <Metric
            label="Overdue"
            value={attention.overdue.count}
            hint="Unfinished tasks past their deadline"
          />
          <Metric
            label={`Due within ${attention.thresholds.soon_days}d`}
            value={attention.due_soon.count}
          />
          <Metric
            label={`Reviews waiting > ${attention.thresholds.stale_days}d`}
            value={attention.stale_reviews.count}
          />
        </div>
        <ThroughputChart points={throughput.points} />
      </section>

      <section className="section-block">
        <div className="section-heading">
          <h2>People</h2>
          <p className="muted">
            Annotated time is measured work. A dash means the volume predates
            time tracking, so the real total is unknowable — it is never zero.
          </p>
        </div>
        {productivity.results.length === 0 ? (
          <div className="empty-state">Nobody is assigned yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Annotator</th>
                  <th>Assigned</th>
                  <th>Approved</th>
                  <th>Mean rounds</th>
                  <th>Mean to submit</th>
                  <th>Annotated time</th>
                </tr>
              </thead>
              <tbody>
                {productivity.results.map((row) => (
                  <tr key={row.user_id}>
                    <td className="cell-name">
                      <Link to={`/people/${row.username}`}>{row.username}</Link>
                    </td>
                    <td>{row.tasks_assigned}</td>
                    <td>{row.tasks_approved}</td>
                    <td>{row.mean_review_rounds ?? "—"}</td>
                    <td>{formatDuration(row.mean_elapsed_to_submit_seconds)}</td>
                    <td>{formatDuration(row.annotated_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {(attention.overdue.count > 0 || attention.stale_reviews.count > 0) && (
        <section className="section-block">
          <div className="section-heading">
            <h2>Needs attention</h2>
          </div>
          {attention.overdue.results.length > 0 && (
            <>
              <h3>Overdue</h3>
              <CategoryBars
                tone="warn"
                rows={attention.overdue.results.slice(0, 8).map((task) => ({
                  label: `${task.volume} (${task.assigned_to ?? "unassigned"})`,
                  value: 1,
                  hint: `Due ${task.deadline}`,
                }))}
              />
            </>
          )}
          {attention.stale_reviews.results.length > 0 && (
            <>
              <h3>Waiting for review</h3>
              <ul className="plain-list">
                {attention.stale_reviews.results.slice(0, 8).map((review) => (
                  <li key={review.id}>
                    <Link to={`/submissions/${review.id}/review`}>
                      {review.volume}
                    </Link>{" "}
                    <span className="muted">
                      from {review.annotator ?? "—"} ·{" "}
                      {new Date(review.submitted_at).toLocaleDateString()}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}
    </>
  );
}
