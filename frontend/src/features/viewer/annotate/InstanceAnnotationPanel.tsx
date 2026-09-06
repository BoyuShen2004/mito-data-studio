import { useCallback, useEffect, useMemo, useState } from "react";
import {
  byLabelId,
  fetchTaskInstanceAnnotations,
  saveInstanceAnnotation,
} from "../../../api/instanceAnnotations";
import { ApiError } from "../../../api/client";
import type {
  InstanceAnnotation,
  InstanceQaFlag,
  InstanceVocabulary,
  MitoMorphology,
} from "../../../types/instanceAnnotation";

/**
 * Morphology and QA flags for the currently Active instance.
 *
 * Two orthogonal controls, deliberately not merged: morphology says what the
 * mitochondrion *is*, the flags say what is wrong with how it is *labelled*.
 * One instance can be both `swollen` and `uncertain`, so a single picker
 * would force the annotator to drop one of the two facts.
 *
 * The panel renders nothing at all when the server reports the feature
 * disabled (503), so an editor on a deployment without it looks exactly as it
 * did before — rather than showing a permanently broken control.
 */

const EMPTY: Omit<InstanceAnnotation, "label_id"> = {
  morphology: "",
  qa_flags: [],
  note: "",
  review_worthy: false,
  updated_by: null,
  updated_at: null,
};

export function useInstanceAnnotations(taskId: number, enabled = true) {
  const [available, setAvailable] = useState(true);
  const [vocabulary, setVocabulary] = useState<InstanceVocabulary | null>(null);
  const [rows, setRows] = useState<Map<number, InstanceAnnotation>>(new Map());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    fetchTaskInstanceAnnotations(taskId)
      .then((response) => {
        if (!alive) return;
        setVocabulary(response.vocabulary);
        setRows(byLabelId(response.annotations));
        setAvailable(true);
      })
      .catch((err) => {
        if (!alive) return;
        // 503 is "this deployment has not enabled it", which is a normal
        // configuration, not an error worth showing anybody.
        if (err instanceof ApiError && err.status === 503) setAvailable(false);
        else setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, [taskId, enabled]);

  const save = useCallback(
    async (labelId: number, patch: Parameters<typeof saveInstanceAnnotation>[2]) => {
      setError(null);
      try {
        const updated = await saveInstanceAnnotation(taskId, labelId, patch);
        setRows((previous) => {
          const next = new Map(previous);
          // A cleared instance comes back with every field empty. Dropping it
          // from the map keeps "in the map" meaning "carries information",
          // which is what the backend's delete-when-empty rule guarantees.
          const carries =
            updated.morphology || updated.qa_flags.length > 0 || updated.note;
          if (carries) next.set(labelId, updated);
          else next.delete(labelId);
          return next;
        });
        return updated;
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        return null;
      }
    },
    [taskId],
  );

  return { available, vocabulary, rows, save, error };
}

export default function InstanceAnnotationPanel({
  taskId,
  activeId,
  readOnly = false,
}: {
  taskId: number;
  /** The Active instance. `null`/0 means nothing is selected. */
  activeId: number | null;
  readOnly?: boolean;
}) {
  const { available, vocabulary, rows, save, error } = useInstanceAnnotations(taskId);
  const [savingField, setSavingField] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState<string | null>(null);

  const current = useMemo(
    () => (activeId ? rows.get(activeId) ?? null : null),
    [rows, activeId],
  );
  const value = current ?? { ...EMPTY, label_id: activeId ?? 0 };

  // Drop an unsaved note draft when the selection moves, so the text never
  // follows the user onto a different instance.
  useEffect(() => {
    setNoteDraft(null);
  }, [activeId]);

  if (!available || !vocabulary) return null;

  if (!activeId) {
    return (
      <section className="instance-annotation-panel" aria-label="Instance details">
        <div className="instance-annotation-title">Instance details</div>
        <p className="muted">Select an instance to record its morphology.</p>
      </section>
    );
  }

  const commit = async (
    field: string,
    patch: Parameters<typeof saveInstanceAnnotation>[2],
  ) => {
    setSavingField(field);
    await save(activeId, patch);
    setSavingField(null);
  };

  const toggleFlag = (flag: InstanceQaFlag) => {
    const next = value.qa_flags.includes(flag)
      ? value.qa_flags.filter((existing) => existing !== flag)
      : [...value.qa_flags, flag];
    void commit(`flag:${flag}`, { qa_flags: next });
  };

  const chooseMorphology = (morphology: MitoMorphology | "") => {
    // Clicking the selected phenotype again clears it, which is the only way
    // to get back to "unclassified" without a separate control.
    const next = value.morphology === morphology ? "" : morphology;
    void commit("morphology", { morphology: next });
  };

  return (
    <section className="instance-annotation-panel" aria-label="Instance details">
      <div className="instance-annotation-title">
        Instance #{activeId}
        {value.review_worthy && (
          <span className="instance-flag-badge" title="Flagged for review">
            ⚑
          </span>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      <fieldset className="instance-annotation-group" disabled={readOnly}>
        <legend>Morphology</legend>
        <div className="instance-chip-row">
          {vocabulary.morphology.map((entry) => (
            <button
              key={entry.value}
              type="button"
              aria-pressed={value.morphology === entry.value}
              className={`instance-chip${
                value.morphology === entry.value ? " instance-chip-active" : ""
              }`}
              disabled={readOnly || savingField === "morphology"}
              onClick={() => chooseMorphology(entry.value as MitoMorphology)}
            >
              {entry.label}
            </button>
          ))}
        </div>
        {!value.morphology && (
          // Said explicitly: a blank phenotype means nobody has looked, which
          // is a different claim from "somebody looked and it was normal".
          <p className="muted instance-annotation-hint">
            Unclassified — not the same as “Normal”.
          </p>
        )}
      </fieldset>

      <fieldset className="instance-annotation-group" disabled={readOnly}>
        <legend>Annotation issues</legend>
        {vocabulary.qa_flags.map((entry) => (
          <label className="instance-flag-row" key={entry.value}>
            <input
              type="checkbox"
              checked={value.qa_flags.includes(entry.value as InstanceQaFlag)}
              disabled={readOnly || savingField === `flag:${entry.value}`}
              onChange={() => toggleFlag(entry.value as InstanceQaFlag)}
            />
            <span>{entry.label}</span>
          </label>
        ))}
      </fieldset>

      <label className="field instance-annotation-note">
        <span>Note</span>
        <input
          value={noteDraft ?? value.note}
          maxLength={280}
          disabled={readOnly}
          placeholder="Optional"
          onChange={(event) => setNoteDraft(event.target.value)}
          onBlur={() => {
            if (noteDraft === null || noteDraft === value.note) return;
            void commit("note", { note: noteDraft });
            setNoteDraft(null);
          }}
        />
      </label>

      {value.updated_by && (
        <p className="muted instance-annotation-hint">
          Last set by {value.updated_by}
        </p>
      )}
    </section>
  );
}
