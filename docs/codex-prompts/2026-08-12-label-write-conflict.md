# Codex task: LabelWriteConflict / stale revision on Save

## Context

Production site: https://mito-data-studio.seg.bio
Code roots on this host:

- Dev / source of truth: `/home/weidf/shenb/mito-data-studio` (git `main`, clean)
- Live deploy dir: `/home/weidf/shenb/mito-data-studio-production-v1.1.1`
  (systemd `mito-data-studio-v1.1.1.service` → gunicorn `127.0.0.1:18191`)

Annotators hit a browser `alert()`:

> This working volume changed in another tab or session. Reload the layer before saving so newer annotation work is not overwritten.

Backend source: `backend/annotation/services.py` — `LabelWriteConflict` when
`expected_revision` ≠ `_working_label_revision(owned_path)`.

Frontend: Save path in `frontend/src/features/viewer/AnnotationCanvas.tsx`
passes `workingLabelRevisionRef` via `expected_revision`, and on failure shows
`window.alert(error.message)`.

## Goal

Investigate whether this is:

1. **Expected multi-tab / multi-session concurrency** (keep the guard), or
2. **A false-positive / UX bug** (same tab races, revision not refreshed after
   whole-volume ops, AI tools, verify, reset, region-only saves, etc.), or
3. **Missing recovery UX** (alert only; no one-click reload of the working
   layer and re-apply of still-pending local edits).

Then implement the smallest safe fix in the **dev tree**
(`/home/weidf/shenb/mito-data-studio`), with tests.

## Hard constraints

- Read and preserve `docs/product-invariants.md` and `AGENTS.md`.
- Do **not** restart, rewrite, or hot-patch production unless the user
  explicitly asks after you present a deploy plan.
- Do **not** weaken the conflict check so that stale clients can overwrite
  newer disk state. Concurrent writers must still lose safely.
- Prefer fixing false positives and recovery UX over removing the guard.
- Keep changes focused; no drive-by refactors.

## Investigation checklist

1. Trace every writer of the working label file and every updater of
   `workingLabelRevisionRef` / response `revision`.
2. Reproduce or reason about same-tab sequences that can leave a stale
   `expected_revision` (Save while another op finishes, tools that mutate
   labels without returning/updating revision, multi-slice save loops).
3. Check HTTP status for `LabelWriteConflict` (should be 409) and whether
   the frontend distinguishes conflict from other errors.
4. Look for existing tests around revision conflicts
   (`test_cellable_port.py`, `test_whole_volume_ops_api.py`, canvas tests).
5. If logs are readable under the production `mito-production-v11` user,
   sample recent 409 / conflict messages; otherwise skip.

## Acceptance

- Clear root-cause writeup (false positive vs real concurrency vs UX gap).
- Code + tests in the dev tree for the chosen fix.
- Short note on how to verify in the viewer (steps an annotator can follow).
- Optional: a **separate** deploy checklist for production-v1.1.1 — do not
  execute it unless asked.

## Start here

```text
backend/annotation/services.py          # LabelWriteConflict, _working_label_revision
backend/annotation/api.py               # expected_revision on slice PUT
frontend/src/features/viewer/AnnotationCanvas.tsx
frontend/src/api/viewer.ts
progress/2026-08-10-manager-viewer-ux-fixes.md
```
