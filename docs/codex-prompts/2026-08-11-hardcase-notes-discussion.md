# Codex Prompt — Hard Case notes: editable primary note + discussion thread (shared backend)

> **How to use:** Paste this whole file into Codex, or use the CLI one-liner at the bottom.
> Work tree: `/home/weidf/shenb/mito-data-studio`
> Production twin: `/home/weidf/shenb/mito-data-studio-production-v1.1.1`
> Related prior brief (UI polish + deploy): `docs/codex-prompts/2026-08-11-seeds-menus-3d-deploy.md`
> Hard-case docs: `docs/guides/sharing-and-hard-cases.md`
> Deploy runbook: `DEPLOYMENT.md`
>
> Re-verify cited paths before editing. After this lands and tests pass,
> **promote to production** (backup → align source → migrate → collectstatic →
> `npm run build:production` → `systemctl reload` → healthz/readyz) per
> `DEPLOYMENT.md`. Do not push git remotes unless asked.

---

## Mission

Hard cases already have a **single create-time `note`** (`HardCase.note`, max
1000 chars). Annotators need a real **notes + discussion** surface:

1. The person who **recorded / shared** the case can **re-edit** that primary note later.
2. On the **Hard Cases list**, add a **Note** button per row.
3. People who did **not** share the case can open Note and **leave a message**.
4. The sharer can also **keep the original note editable** and/or **append
   discussion replies** below.
5. On the **hard-case View / detail** page (and Annotate chrome when that page
   hosts the canvas), Note is a **small button**; click opens a **moderate-sized
   modal** showing the primary note + all discussion messages, with a reply box.
6. **One shared backend + one shared notes UI module.** List “Note”, detail
   “Note”, and annotate-side “Note” are different entry points only — same APIs,
   same modal component, same permissions.

Screenshot intent: Hard Cases inbox rows currently show a truncated note line
and Open / Take down / View. Add **Note** beside those actions; do not rely on
the truncated line alone for editing/discussion.

---

## Current reality (fix against this)

| Piece | Today |
|---|---|
| Model | `HardCase.note` TextField(max_length=1000) — migration `0018_hardcase_note` |
| Create | `POST /api/tasks/<id>/hard-cases/` body `{label_id, note?}` |
| Edit note | **None** |
| Comments / thread | **None** |
| List UI | `HardCaseList.tsx` shows truncated `c.note`; buttons Open/View + Take down |
| Detail UI | `HardCaseDetailPage.tsx` shows inline `Note: {text}` in the topbar |
| Annotate record | `AnnotationCanvas` confirm modal textarea → create only |
| Types / API | `frontend/src/types/hardCase.ts`, `api/hardCases.ts` |
| Permissions | `can_view_hard_case`, `can_annotate_hard_case`, `can_take_down_hard_case` in `services.py` |

---

## Product rules

### Primary note (`HardCase.note`)

- Still the short “why we recorded this” field set at create time (optional).
- **Editable after create** by:
  - the **creator** (`created_by`), and
  - **managers** who can take the case down (same audience as `can_take_down_hard_case` is fine).
- Other project members who can **view** the case may **read** it but not overwrite it.
- Empty note allowed; keep ≤ 1000 chars (or raise slightly if needed — document).

### Discussion messages (new)

- Append-only thread on each hard case (edit-own within a short window optional;
  delete only for author/manager if cheap — v1 can be append-only).
- Any user who `can_view_hard_case` may **post a message**.
- Public token / anonymous share page: **read-only** for notes unless you already
  have an authed session — do **not** invent anonymous write without auth.
- Reasonable body length (e.g. 2000 chars); strip; reject blank.

### Modal UX (shared component)

One component, e.g. `HardCaseNotesModal` / `HardCaseNotesPanel`:

1. **Header:** `Notes · label #{id}` (case id optional).
2. **Primary note section:**
   - If caller may edit primary note: textarea + **Save note**.
   - Else: read-only text (or “No note yet”).
3. **Discussion section:** chronological list (`author · timestamp` + body).
4. **Reply box** at bottom for anyone who can view: textarea + **Post**.
5. Moderate size (not full-screen); Esc / backdrop / Close dismisses.
6. After save/post: refresh in-place; list row preview can refresh via callback.

### Where the Note button appears

| Surface | Control | Behavior |
|---|---|---|
| Hard Cases list (`HardCaseList`) | Button **Note** next to Open/View / Take down | Opens shared modal for that case id |
| Hard case detail / View (`HardCaseDetailPage`) | Small **Note** button in topbar (replace or demote the long inline `Note: …` text) | Same modal |
| Annotate chrome when editing/viewing that hard case (detail page hosts `AnnotationCanvas`) | Same small **Note** control in the shell topbar is enough; if you also add a control inside the canvas chrome, it must call the **same** modal/API | Same modal |
| Record-hard-case confirm dialog | Keep initial optional note at create time; after “Hard case recorded”, offer **Edit notes** that opens the same modal for the new case | Same modal |

List may keep a one-line preview of the primary note under the metadata; editing
happens only through the modal.

---

## Backend design

### Models

1. Keep `HardCase.note` as primary note.
2. Add e.g. `HardCaseMessage` (name flexible):

```text
hard_case FK → HardCase (related_name="messages")
author FK → User (SET_NULL ok)
body TextField
created_at
(optional) updated_at if you allow edit-own
```

Migration additive only.

### Services / permissions

- `update_hard_case_note(case, user, note) -> HardCase` — gate: creator or
  take-down-capable manager; strip; length check.
- `list_hard_case_messages(case)` / `add_hard_case_message(case, user, body)` —
  gate: `can_view_hard_case`.
- Serializer flags (suggested):
  - `can_edit_note: bool`
  - `can_comment: bool` (true when viewer can view; false for pure public token
    if unauthenticated)
  - `message_count: int` (nice for list badge)

### API (REST, auth required unless existing public read)

| Method | Path | Purpose |
|---|---|---|
| `PATCH` or `POST` | `/api/hard-cases/<id>/note/` | Update primary note |
| `GET` | `/api/hard-cases/<id>/messages/` | List discussion messages |
| `POST` | `/api/hard-cases/<id>/messages/` | Add message `{body}` |

Include primary `note` + permission flags on existing `HardCaseSerializer` so the
modal can bootstrap from the list/detail row without an extra round-trip when
possible; still allow GET messages for the thread.

Public hard-case payload may expose **read-only** `note` (already) and optionally
read-only messages; **no** public write.

Wire URLs in the annotation URLConf; follow existing HardCase view style in
`backend/annotation/api.py`.

### Tests

- Creator updates note; other annotator cannot overwrite primary note but can post message.
- Manager can update note (if that is the chosen rule).
- Viewer posts message; appears in GET order.
- Unauthenticated / non-member 403.
- Create-with-note still works; empty note OK.
- Length validation.

---

## Frontend design

### Shared module (required)

- `frontend/src/components/HardCaseNotesModal.tsx` (or `features/hardCases/…`)
- API helpers in `frontend/src/api/hardCases.ts`:
  - `updateHardCaseNote(id, note)`
  - `listHardCaseMessages(id)`
  - `addHardCaseMessage(id, body)`
- Types in `hardCase.ts`: `HardCaseMessage`, extend `HardCase` with
  `can_edit_note`, `can_comment`, `message_count?`.

### Call sites (thin wrappers only)

1. `HardCaseList.tsx` — add **Note** button; open modal; `onChanged` refresh.
2. `HardCaseDetailPage.tsx` — small **Note** button; open same modal.
3. Post-create success UI in `AnnotationCanvas.tsx` — link/button into same modal
   for the newly created case (so sharer can immediately refine the note).
4. Do **not** fork a second notes implementation for Annotate vs list.

### CSS

- Reuse existing modal patterns (hard-case share confirm overlay is a good
  reference in `AnnotationCanvas`).
- List button: `secondary`, same row as Open/View.
- Detail: compact control so it does not blow the topbar (replace long inline
  note text).

### Docs

Update `docs/guides/sharing-and-hard-cases.md`: primary note vs discussion,
who can edit, where the Note button lives.

---

## Production promote (required after green tests)

Same as the previous UI brief:

1. DB backup under the usual `mito-backups/` (or runbook path).
2. Align main tree → `mito-data-studio-production-v1.1.1` (exclude `.env`, `venv`,
   `var`, `logs`, `run`, `node_modules`).
3. `migrate --noinput`, `collectstatic`, `npm run build:production --prefix frontend`.
4. `sudo systemctl reload mito-data-studio-v1.1.1.service`.
5. Health checks; hard-refresh Hard Cases and open Note on list + detail.

If rsync/systemctl is blocked by policy, stop and report exact remaining commands.

---

## Constraints

- Additive migrations only; never reset prod DB.
- No second notes backend for “annotate vs list”.
- Keep create-time optional note in the record dialog.
- Focused diffs; run backend hard-case tests + frontend tests for list/detail/modal.
- Do not push remotes unless asked.

## Suggested order

1. Model + migration + services + API + backend tests  
2. Shared modal + API client  
3. Wire list + detail + post-create entry points  
4. Docs  
5. Production promote + smoke  

## Manual QA

- [ ] Record hard case with note → list shows preview  
- [ ] List **Note** → modal; creator edits primary note → Save → list preview updates  
- [ ] Another project member opens **Note** → cannot overwrite primary (if not manager) → can Post a reply  
- [ ] Creator can also Post replies under the primary note  
- [ ] Detail / View **Note** button opens the **same** modal/data  
- [ ] Public share link does not allow anonymous posting  
- [ ] Production shows Note after deploy + hard refresh  

---

## Start now

Implement the shared notes/discussion system, wire all entry points, test, then
deploy to production per `DEPLOYMENT.md`. Stop with a short report (APIs, who can
edit, backup path, migrate, reload, smoke).

---

## CLI one-liner

```bash
codex exec -C /home/weidf/shenb/mito-data-studio -s danger-full-access \
  "Read and fully execute docs/codex-prompts/2026-08-11-hardcase-notes-discussion.md as the complete task brief (editable primary note + discussion thread, shared modal for list/detail/annotate, then production migrate/reload). Do not push git remotes unless asked."
```

Safer variant:

```bash
codex exec -C /home/weidf/shenb/mito-data-studio -s workspace-write \
  "Read and fully execute docs/codex-prompts/2026-08-11-hardcase-notes-discussion.md. Ask before sudo/systemctl/rsync to production if blocked."
```
