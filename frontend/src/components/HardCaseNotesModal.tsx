import { useEffect, useState } from "react";

import {
  addHardCaseMessage,
  listHardCaseMessages,
  updateHardCaseCategory,
  updateHardCaseNote,
} from "../api/hardCases";
import {
  HARD_CASE_CATEGORIES,
  UNCATEGORISED_LABEL,
  categoryLabel,
} from "../features/viewer/hardCaseCategory";
import type { HardCase, HardCaseMessage } from "../types/hardCase";

/** The quick look at a case from a `WorkList` row: the same discussion the
 * case's own page renders, in a dialog. */
export default function HardCaseNotesModal({
  hardCase,
  onClose,
  onChanged,
}: {
  hardCase: HardCase;
  onClose: () => void;
  onChanged?: () => void;
}) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div className="share-modal-backdrop" onMouseDown={onClose}>
      <div
        className="share-modal hard-case-notes-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`hard-case-notes-title-${hardCase.id}`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="row spread hard-case-notes-heading">
          <h3 id={`hard-case-notes-title-${hardCase.id}`}>Notes · label #{hardCase.label_id}</h3>
          <button type="button" className="secondary" onClick={onClose}>Close</button>
        </div>
        <HardCaseDiscussion hardCase={hardCase} onChanged={onChanged} />
      </div>
    </div>
  );
}

/**
 * A hard case's category, its primary note, and the discussion — the issue
 * conversation, with the reply box at the end of it.
 *
 * One body, two hosts: the case's own page renders it inline under the canvas,
 * and `HardCaseNotesModal` wraps it for the quick look from a `WorkList` row.
 * Extracted so those two can never drift into two different discussions.
 */
export function HardCaseDiscussion({
  hardCase: initialCase,
  onChanged,
  headings = "h4",
}: {
  hardCase: HardCase;
  onChanged?: () => void;
  /** `h3` on the case page, where the discussion is the page's own section. */
  headings?: "h3" | "h4";
}) {
  const Heading = headings;
  const [hardCase, setHardCase] = useState(initialCase);
  const [note, setNote] = useState(initialCase.note);
  const [messages, setMessages] = useState<HardCaseMessage[]>([]);
  const [reply, setReply] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Track the row the host re-fetched, so a take-down elsewhere on the page is
  // reflected here without remounting and losing a half-typed reply.
  useEffect(() => {
    setHardCase(initialCase);
    setNote(initialCase.note);
  }, [initialCase]);

  useEffect(() => {
    let live = true;
    setLoading(true);
    listHardCaseMessages(initialCase.id)
      .then((rows) => {
        if (live) setMessages(rows);
      })
      .catch((e: unknown) => {
        if (live) setError(e instanceof Error ? e.message : "Could not load discussion.");
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => { live = false; };
  }, [initialCase.id]);

  const saveCategory = async (next: string) => {
    setBusy(true);
    setError(null);
    try {
      const updated = await updateHardCaseCategory(hardCase.id, next);
      setHardCase(updated);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change the category.");
    } finally {
      setBusy(false);
    }
  };

  const saveNote = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await updateHardCaseNote(hardCase.id, note);
      setHardCase(updated);
      setNote(updated.note);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save note.");
    } finally {
      setBusy(false);
    }
  };

  const postReply = async () => {
    const body = reply.trim();
    if (!body) return;
    setBusy(true);
    setError(null);
    try {
      const created = await addHardCaseMessage(hardCase.id, body);
      setMessages((rows) => [...rows, created]);
      setReply("");
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not post message.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className="hard-case-category-section">
        <Heading>Category</Heading>
        {hardCase.can_edit_note ? (
          <div className="hard-case-category-row">
            {HARD_CASE_CATEGORIES.map((entry) => (
              <button
                key={entry.value}
                type="button"
                disabled={busy}
                aria-pressed={hardCase.category === entry.value}
                title={entry.hint}
                className={`hard-case-category-chip${
                  hardCase.category === entry.value ? " hard-case-category-chip-active" : ""
                }`}
                onClick={() =>
                  // Same rule as the record dialog: clicking the current one
                  // clears it, because "we are not sure why" is a real answer.
                  void saveCategory(hardCase.category === entry.value ? "" : entry.value)
                }
              >
                {entry.label}
              </button>
            ))}
          </div>
        ) : (
          <p className={hardCase.category ? "" : "muted"}>
            {hardCase.category ? categoryLabel(hardCase.category) : UNCATEGORISED_LABEL}
          </p>
        )}
      </section>

      <section className="hard-case-primary-note">
        <Heading>Primary note</Heading>
        {hardCase.can_edit_note ? (
          <>
            <textarea
              aria-label="Primary note"
              value={note}
              maxLength={1000}
              rows={3}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Why was this case recorded?"
            />
            <div className="row hard-case-note-save-row">
              <button
                type="button"
                onClick={() => void saveNote()}
                disabled={busy || note === hardCase.note}
              >
                Save note
              </button>
              <span className="muted">{note.length}/1000</span>
            </div>
          </>
        ) : (
          <p className={hardCase.note ? "" : "muted"}>{hardCase.note || "No note yet."}</p>
        )}
      </section>

      <section className="hard-case-discussion">
        <Heading>
          Discussion
          {Math.max(hardCase.message_count, messages.length)
            ? ` (${Math.max(hardCase.message_count, messages.length)})`
            : ""}
        </Heading>
        <div className="hard-case-message-list" aria-live="polite">
          {loading && <p className="muted">Loading discussion…</p>}
          {!loading && messages.length === 0 && <p className="muted">No replies yet.</p>}
          {messages.map((message) => (
            <article key={message.id} className="hard-case-message">
              <div className="muted hard-case-message-meta">
                {message.author_username || "Former user"} ·{" "}
                {new Date(message.created_at).toLocaleString()}
              </div>
              <p>{message.body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* The reply box sits where the history ends — you read, then you act. */}
      {hardCase.can_comment && (
        <div className="hard-case-reply">
          <textarea
            aria-label="Discussion reply"
            value={reply}
            maxLength={2000}
            rows={3}
            onChange={(event) => setReply(event.target.value)}
            placeholder="Write a reply"
          />
          <div className="row spread">
            <span className="muted">{reply.length}/2000</span>
            <button type="button" onClick={() => void postReply()} disabled={busy || !reply.trim()}>
              Post
            </button>
          </div>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </>
  );
}
