import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { createProject } from "../api/projects";
import { ANNOTATION_TYPES } from "../labels";
import type { AnnotationType } from "../types";

/** Step 1 of starting new work: create the project.
 *
 * Data is registered *into* a project, so the project is created first and
 * described on its own terms (what is being annotated, by when) rather than
 * being conjured out of whichever dataset happened to be registered first.
 * On success this continues straight to step 2, registering data into it.
 */
export default function NewProjectPage() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [annotationType, setAnnotationType] = useState<AnnotationType>(
    "instance_segmentation",
  );
  const [annotationTarget, setAnnotationTarget] = useState("mitochondria");
  const [deadline, setDeadline] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent, thenRegister: boolean) => {
    e.preventDefault();
    if (!title.trim()) {
      setError("A project title is required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const project = await createProject({
        title: title.trim(),
        description: description.trim(),
        annotation_type: annotationType,
        annotation_target: annotationTarget.trim() || "mitochondria",
        deadline: deadline || null,
      });
      navigate(
        thenRegister
          ? `/register-data?project=${project.id}`
          : `/projects/${project.id}`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create project");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <h1>New project</h1>

      {error && <div className="error">{error}</div>}

      <form onSubmit={(e) => submit(e, true)}>
        <div className="card">
          <h3>Project</h3>
          <label className="field">
            <span>Project title *</span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Project title"
              autoFocus
              required
            />
          </label>
          <label className="field">
            <span>Description</span>
            <textarea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What is this work for?"
            />
          </label>
          <div className="row fields">
            <label className="field">
              <span>Annotation type</span>
              <select
                value={annotationType}
                onChange={(e) =>
                  setAnnotationType(e.target.value as AnnotationType)
                }
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
        </div>

        <div className="row">
          <button type="submit" disabled={busy}>
            {busy ? "Creating…" : "Create project & register data →"}
          </button>
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={(e) => submit(e, false)}
          >
            Create project only
          </button>
        </div>
      </form>
    </>
  );
}
