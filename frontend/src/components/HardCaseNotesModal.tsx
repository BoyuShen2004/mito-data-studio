import { useEffect, useState } from "react";

import {
  addHardCaseMessage,
  listHardCaseMessages,
  updateHardCaseNote,
} from "../api/hardCases";
import type { HardCase, HardCaseMessage } from "../types/hardCase";

export default function HardCaseNotesModal({
  hardCase: initialCase,
  onClose,
  onChanged,
}: {
  hardCase: HardCase;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const [hardCase, setHardCase] = useState(initialCase);
  const [note, setNote] = useState(initialCase.note);
  const [messages, setMessages] = useState<HardCaseMessage[]>([]);
  const [reply, setReply] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

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

        <section className="hard-case-primary-note">
          <h4>Primary note</h4>
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
                <button type="button" onClick={() => void saveNote()} disabled={busy || note === hardCase.note}>
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
          <h4>Discussion{Math.max(hardCase.message_count, messages.length) ? ` (${Math.max(hardCase.message_count, messages.length)})` : ""}</h4>
          <div className="hard-case-message-list" aria-live="polite">
            {loading && <p className="muted">Loading discussion…</p>}
            {!loading && messages.length === 0 && <p className="muted">No replies yet.</p>}
            {messages.map((message) => (
              <article key={message.id} className="hard-case-message">
                <div className="muted hard-case-message-meta">
                  {message.author_username || "Former user"} · {new Date(message.created_at).toLocaleString()}
                </div>
                <p>{message.body}</p>
              </article>
            ))}
          </div>
        </section>

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
      </div>
    </div>
  );
}
