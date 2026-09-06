import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { fetchDeliveryOverview } from "../api/milestones";
import type { ProjectDelivery } from "../types/milestone";

/**
 * Overdue work, work due soon, and reviews that have been waiting — across
 * every project at once.
 *
 * The per-project Delivery tab answers "how is this project doing". This
 * answers "where should I look first", which a manager cannot get by opening
 * projects one at a time.
 *
 * Renders an explicit notice when the feature is off rather than an error, and
 * nothing at all for a non-manager (the endpoint is manager-only, and a
 * silently empty panel would read as "nothing is overdue").
 */
export default function AttentionPanel() {
  const [data, setData] = useState<ProjectDelivery | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "off" | "denied">(
    "loading",
  );

  useEffect(() => {
    let alive = true;
    fetchDeliveryOverview()
      .then((result) => {
        if (!alive) return;
        setData(result);
        setState("ready");
      })
      .catch((err) => {
        if (!alive) return;
        if (err instanceof ApiError && err.status === 503) setState("off");
        else if (err instanceof ApiError && err.status === 403) setState("denied");
        else setState("off");
      });
    return () => {
      alive = false;
    };
  }, []);

  if (state === "denied") return null;
  if (state === "loading") return <p className="muted">Loading…</p>;
  if (state === "off" || !data) {
    return (
      <div className="empty-state">
        Delivery analytics are not enabled on this deployment.
      </div>
    );
  }

  const { attention } = data;
  const clear =
    attention.overdue.count === 0 &&
    attention.due_soon.count === 0 &&
    attention.stale_reviews.count === 0;

  return (
    <>
      <div className="section-heading">
        <h2>Needs attention</h2>
        <p className="muted">Across every project, not just one.</p>
      </div>

      <div className="summary-strip">
        <div
          className={`summary-metric${attention.overdue.count ? " summary-metric-warn" : ""}`}
        >
          <strong>{attention.overdue.count}</strong>
          <span>Overdue</span>
        </div>
        <div className="summary-metric">
          <strong>{attention.due_soon.count}</strong>
          <span>Due within {attention.thresholds.soon_days}d</span>
        </div>
        <div
          className={`summary-metric${attention.stale_reviews.count ? " summary-metric-warn" : ""}`}
        >
          <strong>{attention.stale_reviews.count}</strong>
          <span>Waiting &gt; {attention.thresholds.stale_days}d</span>
        </div>
      </div>

      {clear ? (
        <div className="empty-state">Nothing is overdue or waiting.</div>
      ) : (
        <>
          {attention.overdue.results.length > 0 && (
            <section className="section-block">
              <h3>Overdue</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Volume</th>
                      <th>Project</th>
                      <th>Annotator</th>
                      <th>Due</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {attention.overdue.results.map((task) => (
                      <tr key={task.id}>
                        <td className="cell-name">{task.volume}</td>
                        <td>{task.project}</td>
                        <td>{task.assigned_to ?? "—"}</td>
                        <td>{task.deadline ?? "—"}</td>
                        <td>
                          <Link to={`/tasks/${task.id}`}>Open</Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {attention.stale_reviews.results.length > 0 && (
            <section className="section-block">
              <h3>Waiting for review</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Volume</th>
                      <th>Annotator</th>
                      <th>Submitted</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {attention.stale_reviews.results.map((review) => (
                      <tr key={review.id}>
                        <td className="cell-name">{review.volume}</td>
                        <td>{review.annotator ?? "—"}</td>
                        <td>
                          {new Date(review.submitted_at).toLocaleDateString()}
                        </td>
                        <td>
                          <Link to={`/submissions/${review.id}/review`}>
                            Review
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </>
  );
}
