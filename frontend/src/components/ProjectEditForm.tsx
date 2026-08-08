import { useState } from "react";
import { updateProject } from "../api/projects";
import { ANNOTATION_TYPES, PROJECT_STATUSES, titleize } from "../labels";
import type { Project } from "../types/project";

/** Edit a project's own fields. Dataset-level details live on each dataset. */
export default function ProjectEditForm({
  project,
  onSaved,
}: {
  project: Project;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(project.title);
  const [description, setDescription] = useState(project.description ?? "");
  const [annotationType, setAnnotationType] = useState(project.annotation_type);
  const [annotationTarget, setAnnotationTarget] = useState(
    project.annotation_target ?? "",
  );
  const [status, setStatus] = useState(project.status);
  const [deadline, setDeadline] = useState(project.deadline ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await updateProject(project.id, {
        title,
        description,
        annotation_type: annotationType,
        annotation_target: annotationTarget,
        status,
        // An empty date field means "no deadline", not the empty string.
        deadline: deadline || null,
      });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card edit-form">
      <h3>Edit project</h3>
      {error && <div className="error">{error}</div>}
      <div className="row fields">
        <label className="field" style={{ flex: 2 }}>
          <span>Title</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="field" style={{ flex: 1 }}>
          <span>Status</span>
          <select value={status} onChange={(e) => setStatus(e.target.value as Project["status"])}>
            {PROJECT_STATUSES.map((s) => (
              <option key={s} value={s}>
                {titleize(s)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="row fields">
        <label className="field">
          <span>Annotation type</span>
          <select
            value={annotationType}
            onChange={(e) => setAnnotationType(e.target.value)}
          >
            {ANNOTATION_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Annotation target</span>
          <input
            value={annotationTarget}
            onChange={(e) => setAnnotationTarget(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Deadline</span>
          <input
            type="date"
            value={deadline}
            onChange={(e) => setDeadline(e.target.value)}
          />
        </label>
      </div>
      <label className="field">
        <span>Description</span>
        <textarea
          rows={2}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <button type="button" onClick={save} disabled={busy}>
        {busy ? "Saving…" : "Save project"}
      </button>
    </div>
  );
}
