# Codex Prompt — Seeds chrome, label/canvas context menus, 3D bulk undo, then production deploy

> **How to use:** Paste this whole file into Codex, or run the CLI one-liner at the bottom.
> Work tree: `/home/weidf/shenb/mito-data-studio`
> Production twin: `/home/weidf/shenb/mito-data-studio-production-v1.1.1`
> Deploy runbook: `/home/weidf/shenb/mito-data-studio/DEPLOYMENT.md`
>
> Re-verify cited paths before editing. After UI/code work lands and tests pass,
> **you are authorized to promote to production** (rsync-aligned source, migrate,
> collectstatic, frontend build, systemctl reload, health checks) following
> `DEPLOYMENT.md`. Take a DB backup first. Do not push to git remotes unless asked.
> Do not touch unrelated systemd units.

---

## Mission

Four small Annotate UI polish fixes from screenshots, then **redeploy production**
so users actually see them (last round stayed on the main tree only).

Screenshots for layout intent (if attached in chat, prefer them):

1. Seeds tool row before any seed is placed — only `Clear seeds` + `Run Watershed`,
   both disabled / greyed.
2. Seeds tool row after a seed is on the canvas — same two buttons enabled;
   status `Target label N · K seed(s)` sits to the **right** of `Run Watershed`.
3. Labels list row context menu currently shows Verify / Unverify plus a second
   row Hide 3D / Solo — Labels must keep **only** Verify / Unverify.

---

## (1) Seeds toolbar: fixed buttons + status on the right

**File:** `frontend/src/features/viewer/annotate/AnnotateToolChrome.tsx`
(and any CSS in `frontend/src/styles.css` for `.tool-context` / seeds row).

**Desired layout whenever `paintTool === "seeds"`:**

```
[ Clear seeds ]  [ Run Watershed ]  Target label 59 · 1 seed(s)
```

Rules:

1. **`Clear seeds` and `Run Watershed` always occupy fixed positions** in the
   Seeds context row (do not appear/disappear; do not jump when the status text
   mounts). Prefer a reserved flex/grid slot so the buttons never shift left/right
   when the status string is empty vs present.
2. Both buttons are **disabled until at least one seed has been dropped on the
   canvas** (`wsSeedCount === 0` → disabled). While `wsRunning`, Run Watershed
   stays disabled / shows `Splitting…` as today.
3. Status text `Target label {id} · {n} seed(s)` renders **only when**
   `wsTargetLabel != null` (or whenever there is something useful to show), and
   always to the **right of Run Watershed** — never to the left of the buttons
   (current code puts the status first; move it after the buttons).
4. Keep Seeds free of Active/New (already hidden for seeds). Do not reintroduce them.
5. Update / add a small chrome test if one exists for Seeds; otherwise a focused
   AnnotateToolChrome test asserting order: Clear → Run → status.

---

## (2) Labels list right-click: Verify / Unverify only

**File:** `frontend/src/features/viewer/LabelsPanel.tsx` (`rowMenu` block ~592–648).

**Desired:** Labels row context menu contains **only**:

- Verify (disabled when already verified)
- Unverify (disabled when not verified)

**Remove** from this menu: `Hide 3D` / `Show 3D`, `Solo` / `Unsolo`, and the `<hr />`
that separated lifecycle from view actions.

Per-row inline `LabelViewButtons` (3D / Solo / Hide eye) stay as they are — only
the **right-click menu** is lifecycle-only.

Update `LabelsPanel` tests accordingly.

---

## (3) Canvas right-click on a label: 2×2 four-button block (like current Labels menu)

**File:** `frontend/src/features/viewer/AnnotationCanvas.tsx` (canvas
`contextMenu` ~6464+), plus CSS for `.canvas-context-menu` if needed.

Today, when right-click hits a label, the bottom section is a vertical stack like:

- ✓ Verify label {id}
- ○ Solo label {id}
- Show/Hide 3D label {id}

**Desired:** Keep the existing tool grid (Cancel + paint tools) above. Replace
the label-specific bottom section with the **same 2×2 four-button layout** that
Labels currently uses (before item 2 removes the bottom row from Labels):

```
[ Verify ]   [ Unverify ]
[ Show 3D / Hide 3D ]   [ Solo / Unsolo ]
```

Details:

1. Wire Verify / Unverify through existing lifecycle (`handleLifecycleAction`),
   with the same enable/disable rules as Labels.
2. Show 3D / Hide 3D toggles `pinned3D` for that id (same as current canvas
   Show 3D).
3. Solo / Unsolo toggles solo for that id.
4. Prefer short labels (`Verify`, `Unverify`, `Show 3D`, `Solo`) for the 2×2;
   put the id in `title` / aria if useful.
5. CSS: make this label-action block a **2-column grid** so it visually matches
   the Labels row menu look from the screenshot (not a single tall column).
6. If `labelId == null` (right-click empty canvas), omit this block entirely.

Optional cleanup: extract a tiny shared `LabelContextActions` component used by
canvas (and previously by Labels) so the 2×2 does not drift — only if cheap.

Update canvas / paintTools tests as needed.

---

## (4) Labels: undo / clear “3D all”

**File:** `frontend/src/features/viewer/LabelsPanel.tsx` (sticky header next to
`3D layer` / `3D all`), state owners in `AnnotationCanvas.tsx` (`pinned3D`,
`onPinMany`, clear-pins helper).

**Desired (pick the clearest UX; prefer A unless B is already half-built):**

**A (preferred):** Keep `3D all` / `3D layer`. Add a sibling button
**`Clear 3D`** (or `Hide 3D all`) that clears every pinned 3D id
(`setPinned3D(new Set())` or existing clear helper). Disabled when nothing is
pinned.

**B (alternative):** Make `3D all` a toggle — when the current listed set is
already fully pinned, the same control becomes **`Hide 3D all`** and clears
them.

Same idea for **This layer** + `3D layer`: clearing should at least clear all
pins, or clear pins that came from the bulk action — full clear of `pinned3D`
is acceptable and simplest.

Do not remove per-row 3D toggles.

Add a short test for the clear/toggle control.

---

## (5) After code lands: promote to production

User requirement: **昨晚/本轮修改后要重新部署成为 production、migrate 等。**

Follow `/home/weidf/shenb/mito-data-studio/DEPLOYMENT.md` carefully:

1. **Backup** production DB / note backup path before migrate.
2. Align application source from main tree →
   `/home/weidf/shenb/mito-data-studio-production-v1.1.1`
   (rsync or equivalent). **Exclude** `.env`, `venv/`, `var/`, `logs/`, `run/`,
   `node_modules/`, local DBs, `__pycache__`. Never delete runtime data dirs.
3. If Python deps or migrations changed:
   ```bash
   cd /home/weidf/shenb/mito-data-studio-production-v1.1.1
   venv/bin/pip install -r requirements-release.txt   # only if needed
   cd backend
   ../venv/bin/python manage.py migrate --noinput
   ../venv/bin/python manage.py collectstatic --noinput
   ```
4. Frontend:
   ```bash
   cd /home/weidf/shenb/mito-data-studio-production-v1.1.1
   npm run build:production --prefix frontend
   ```
5. Reload web unit (graceful after source/static):
   ```bash
   sudo systemctl reload mito-data-studio-v1.1.1.service
   ```
6. Health checks (`/healthz`, `/readyz` on the production bind — see runbook /
   prior deploy notes; gunicorn is `127.0.0.1:18191`).
7. Smoke the four UI items on the **production** URL (hard refresh), not only
   the main tree.

If a permission classifier blocks rsync/systemctl, **stop and ask** rather than
claiming deploy succeeded. Report exact commands still needed.

---

## Engineering constraints

- Implement in `/home/weidf/shenb/mito-data-studio` first; production is the
  promote target, not a second feature fork.
- Keep diffs focused on these UI items + deploy; no unrelated refactors.
- Match existing styles; avoid purple AI-default chrome churn.
- Run focused vitest/typecheck for touched frontend files.
- Do not commit secrets; do not `git push` unless asked.

## Suggested order

1. (1) Seeds chrome layout
2. (2) Labels menu trim
3. (3) Canvas 2×2 label actions
4. (4) Clear 3D / Hide 3D all
5. Tests
6. (5) Production promote + migrate + reload + smoke

## Deliverables

1. Code + tests for (1)–(4).
2. Production deploy completed **or** blocked with the exact remaining commands.
3. Short summary: what changed, backup path, migrate applied?, reload OK?,
   healthz/readyz, manual smoke results for Seeds / Labels menu / canvas menu /
   Clear 3D.

## Manual QA

- [ ] Seeds: buttons fixed in place; disabled with 0 seeds; enabled after drop
- [ ] Status text appears to the **right** of Run Watershed and does not shove buttons
- [ ] Labels right-click: only Verify + Unverify
- [ ] Canvas right-click on mito: 2×2 Verify / Unverify / Show|Hide 3D / Solo|Unsolo
- [ ] 3D all then Clear 3D (or toggle Hide) clears the 3D pins
- [ ] Production site shows the above after hard refresh

---

## Start now

Implement (1)–(4), test, then execute (5) per `DEPLOYMENT.md`. Stop with a concise
deploy report.

---

## CLI one-liner

```bash
codex exec -C /home/weidf/shenb/mito-data-studio -s danger-full-access \
  "Read and fully execute docs/codex-prompts/2026-08-11-seeds-menus-3d-deploy.md as the complete task brief (UI items 1–4, then production promote/migrate/reload per DEPLOYMENT.md). Do not push git remotes unless asked."
```

If you prefer not to give full sandbox bypass, use `-s workspace-write` and
explicitly approve systemctl/rsync when prompted:

```bash
codex exec -C /home/weidf/shenb/mito-data-studio -s workspace-write \
  "Read and fully execute docs/codex-prompts/2026-08-11-seeds-menus-3d-deploy.md (items 1–4 + production deploy). Ask before any command that needs sudo/systemctl if blocked."
```
