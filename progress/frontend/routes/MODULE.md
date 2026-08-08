# `frontend/src/routes/` — role-gated routing

## `roles.ts`

`HomeRole = "manager" | "requester" | "annotator"` — the three routing
buckets (note: this collapses the backend's `client`→`requester` legacy
mapping the same way `accounts.roles` does). Helpers:

- `effectiveRole(role)`
- `homePathForRole(role)` → `/manager`, `/requester`, or `/annotator`
- `homeLabelForRole(role)` → `"Dashboard"` / `"My Projects"` / `"My Tasks"`
  (the human label used on Home / Done buttons)

Re-exported from `AppRoutes.tsx` so other modules can
`import { ... } from "../routes/AppRoutes"` without an import cycle (the
actual definitions live here specifically to avoid `Layout`/`Navbar`
importing from `AppRoutes.tsx`, which itself imports `Layout`).

## `AppRoutes.tsx`

The single route table. `RequireAuth({children, roles?, fullBleed?})` —
redirects to `/login` if not authenticated (after `AuthContext`'s initial
loading resolves), or to the user's home path if `roles` is given and
doesn't include their `effectiveRole`. Every authenticated route wraps its
page in `<Layout>`:

- **default** — navbar + centered `.container` (max-width 1080px)
- **`fullBleed`** — navbar kept, but `.container` skipped so View/Annotate
  can fill the remaining window under the navbar (`.editor-shell` inside
  `.full-bleed-main`). Replaces the old `RequireAuthBare` that stripped the
  navbar entirely.

Route groups:
- **Public (no auth, no `Layout`/navbar)**: `/login`, `/register`, and
  `/share/hard-case/:token` → `HardCaseSharePage` — the public, no-account
  read-only viewer for a hard case's token link. Registered **outside** `RequireAuth` so a recipient
  with no account can open it. It mounts the same `ViewerShell` the authed
  viewer routes use — with `standalone`, since there is no `Layout fullBleed`
  above it to supply the viewport height — and drives the shared
  `AnnotationCanvas` in view-only mode via the public token endpoints. See
  `../pages/MODULE.md`, `../components/MODULE.md`, `../features/MODULE.md`,
  `progress/history/02-share-hard-case.md`, and
  `progress/history/03-fix-hard-case-share-view.md`.
- **Manager**: `/manager` (dashboard), `/projects` (list — manager-only,
  requesters use their own dashboard instead).
- **Requester**: `/requester`.
- **Shared manager+requester**: `/register-data`, `/projects/new`,
  `/projects/:id`, `/volumes/:id` — a requester can only actually act on
  projects/volumes they own (enforced backend-side, not by this routing
  layer — the route just admits the *role*, ownership is a second check).
- **Manager-only extra**: `/submissions/:id/review`.
- **Annotator** (+manager, since `is_annotator` includes managers):
  `/annotator`, `/tasks/:id/submit`.
- **Shared (any authenticated role)**: `/tasks/:id` (task detail),
  `/people` + `/people/:username` (the collaboration surface), and
  `/hard-cases` (the inbox). These carry **no route-level `roles` gate on
  purpose** — the backend scopes what's in them (role for People, project
  membership for hard cases), so a role gate here would only be a second,
  drift-prone copy of that decision.
- **Hard case detail** (`RequireAuth fullBleed`): `/hard-cases/:id` →
  `HardCaseDetailPage`, which mounts the same `AnnotationCanvas` the editor
  does. Whether it is editable comes from the API's `can_annotate`, not from
  the route.
- **Visualization** (`RequireAuth fullBleed`) — global navbar present:
  - `/viewer/volumes/:id` → `VolumeViewerPage` (any role).
  - `/viewer/tasks/:id` → `TaskViewerPage` (any role; read-only unless the
    page's own internal `mayEdit` check passes, and even then only shows
    the editor when the route was `/editor/...`).
  - `/editor/tasks/:id` → `TaskViewerPage editable` — **route-gated** to
    `roles={["manager", "annotator"]}` (a requester can never even land
    here), and **further gated inside the page** to the assigned annotator
    specifically or a manager (`mayEdit = isManager || task.assigned_to ===
    user?.id`) — an annotator hitting another annotator's task by URL
    falls back to the read-only viewer rather than an error page. See
    `../pages/MODULE.md`'s `ViewerPage.tsx` entry.
- `*` → redirect to `/`.

`HomeRedirect` (the `/` route) sends an authenticated user to their role's
home path; `RequireAuth` handles the loading state so this never flashes a
login redirect before `AuthContext` has resolved the current user.

## `backNavigation.ts`

`backFallbackFor(pathname, role)` — where the shared `<BackButton>` should
go when there's no in-app browser history to pop (deep link, page reload,
or first page after login). Not a generic "go up one path segment" — it
understands the actual route hierarchy:

- task submit form → that task
- `/viewer/tasks/:id` or `/editor/tasks/:id` → `/tasks/:id`
- `/viewer/volumes/:id` → `/volumes/:id`
- `/hard-cases/:id` → `/hard-cases`; `/people/:username` → `/people`
- project/volume detail → `/projects` for a manager, else role home
- everything else (incl. `/people`, `/hard-cases`) → role home

Returns `null` on a role's home page itself (nothing above it) — Navbar
only renders ← Back when this returns non-null.

**Back is not the "go home" control.** Role home is brand + left nav
(Dashboard / My Projects / My Tasks). Viewer/Annotate page topbars must
not re-add Done/Home/Task-details — see
`../components/MODULE.md`.

## Gotchas

- Route-level `roles` gating and in-page ownership/assignment checks are
  **both** necessary — don't assume a route being reachable means the
  action is allowed; every page that mutates data re-checks against the
  actual object (see `../pages/MODULE.md` for which pages do what checks).
- If you add a new route under an existing hierarchy (e.g. another
  `/tasks/:id/...` page), update `backNavigation.ts` too, or the back
  button will silently fall back to the role home instead of the more
  specific parent.
- Do **not** revive `RequireAuthBare` to strip the navbar on viewer routes —
  the product decision is that View/Annotate keep the global chrome so Home
  / Back stay reachable.
