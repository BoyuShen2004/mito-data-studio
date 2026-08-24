import { Link } from "react-router-dom";
import { displayTaskLayerRange } from "../features/viewer/layerIndex";
import { labelColorCss } from "../features/viewer/labelColor";
import { withViewLocation, type ViewLocation } from "../features/viewer/viewLocation";
import type { ReviewLabelComment } from "../types/reviewLabelComment";

function commentHref(comment: ReviewLabelComment, mode: "view" | "annotate"): string {
  const path = mode === "annotate" ? "editor" : "viewer";
  const base =
    `/${path}/tasks/${comment.task}` +
    `?feedback=${comment.id}&submission=${comment.submission}`;
  if (
    comment.view_z == null ||
    comment.view_y == null ||
    comment.view_x == null
  ) {
    return `${base}&label=${comment.label_id}`;
  }
  const axis =
    comment.view_axis === "x" || comment.view_axis === "y" ? comment.view_axis : "z";
  const location: ViewLocation = {
    z: comment.view_z,
    y: comment.view_y,
    x: comment.view_x,
    axis,
    label: comment.label_id,
  };
  const absolute = withViewLocation(base, location);
  const url = new URL(absolute, window.location.origin);
  return `${url.pathname}${url.search}`;
}

function confirmAndDelete(
  comment: ReviewLabelComment,
  onDelete?: (comment: ReviewLabelComment) => void | Promise<void>,
) {
  if (
    !window.confirm(`Delete the comment on label #${comment.label_id}?`)
  ) {
    return;
  }
  void onDelete?.(comment);
}

export default function ReviewLabelCommentList({
  comments,
  showProject = false,
  title,
  canDelete = false,
  onDelete,
  emptyText = "No label comments yet — open View and right-click a mitochondria.",
}: {
  comments: ReviewLabelComment[];
  showProject?: boolean;
  /** When set, rendered on one line with Delete (manager review page). */
  title?: string;
  /** Managers on the review page may delete comments. */
  canDelete?: boolean;
  onDelete?: (comment: ReviewLabelComment) => void | Promise<void>;
  emptyText?: string;
}) {
  const heading =
    title || canDelete ? (
      <div
        className="row spread review-label-comments-heading"
        style={{ alignItems: "center", marginBottom: comments.length ? "0.65rem" : 0 }}
      >
        {title ? <h3 style={{ margin: 0 }}>{title}</h3> : <span />}
        {canDelete && comments.length > 0 && (
          <div className="row task-actions" style={{ marginLeft: "auto" }}>
            {comments.map((comment) => (
              <button
                key={comment.id}
                type="button"
                className="danger"
                title={`Delete comment on label #${comment.label_id}`}
                onClick={() => confirmAndDelete(comment, onDelete)}
              >
                {comments.length > 1 ? `Delete #${comment.label_id}` : "Delete"}
              </button>
            ))}
          </div>
        )}
      </div>
    ) : null;

  if (comments.length === 0) {
    return (
      <>
        {heading}
        <p className="muted" style={{ marginBottom: 0 }}>{emptyText}</p>
      </>
    );
  }

  return (
    <>
      {heading}
      <ul className="hard-case-list review-label-comment-list">
        {comments.map((comment) => {
          const viewHref = commentHref(comment, "view");
          const annotateHref = commentHref(comment, "annotate");
          return (
            <li className="hard-case-row" key={comment.id}>
              <div className="row spread">
                <div>
                  <Link to={viewHref}>
                    <strong>
                      <span
                        className="label-swatch"
                        style={{ background: labelColorCss(comment.label_id) }}
                        aria-hidden="true"
                      />{" "}
                      Label #{comment.label_id}
                    </strong>
                  </Link>
                  <div className="muted" style={{ fontSize: "0.78rem" }}>
                    {showProject && comment.project_title ? `${comment.project_title} · ` : ""}
                    {comment.volume_name || "volume"} · z
                    {displayTaskLayerRange(comment.z_start, comment.z_end)} · task #
                    {comment.task}
                    {` · round ${comment.round_number}`}
                    {comment.view_z != null
                      ? ` · commented at layer ${comment.view_z + 1}`
                      : ""}
                  </div>
                  <div className="hard-case-note" title={comment.body}>{comment.body}</div>
                  <div className="muted" style={{ fontSize: "0.78rem" }}>
                    by {comment.author_username || "—"} ·{" "}
                    {new Date(comment.updated_at).toLocaleString()}
                  </div>
                </div>
                <div className="row task-actions review-label-comment-actions">
                  <Link to={viewHref}>
                    <button type="button" className="secondary">View</button>
                  </Link>
                  <Link to={annotateHref}>
                    <button type="button">Annotate</button>
                  </Link>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}
