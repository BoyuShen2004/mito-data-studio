# Product invariants

These behaviors are intentional product requirements. Do not remove, hide, or
reinterpret them during UI cleanup, authentication refactors, or release work
without explicit user approval and updated regression tests.

## Development accounts on the login page

- The **Development accounts** section is gated by the frontend build flag
  `VITE_SHOW_DEMO_ACCOUNTS=true` and the backend allowlist. DEV enables it;
  production builds disable it and never fetch mock credentials. When enabled,
  it appears directly below “Need an
  account? Create one as an annotator or a requester.”
- It lists the seven server-allowlisted accounts in configured order.
- Selecting an account fills username and password and selects the correct
  portal tab. It does **not** authenticate, submit the form, or navigate.
- The user must explicitly press **Sign in**.
- Resetting application data clears the accounts' projects, datasets, volumes,
  tasks, annotations, shares, jobs, tokens, and sessions, but preserves the
  seven account identities.

## Destructive reset

- A red **Clear all existing files** entry lives inside the login page's
  **Development accounts** section, not in the authenticated navigation.
- Selecting it leaves the ordinary account form untouched and shows one native
  destructive-action confirmation. It never asks for or inserts an
  administrator password and never signs the user in as an administrator.
- The passwordless route exists only when both `ENABLE_MOCK_DEV_LOGIN` and
  `MITO_ALLOW_DEV_RESET` are enabled and development identities are configured;
  otherwise it returns 404. Its POST requires CSRF plus the confirmation value
  obtained from its status request. It uses the same comprehensive data/file
  cleanup and external-file protections as the production reset.
- The separate authenticated production reset retains its password re-check,
  exact phrase, single-use token, backup freshness, maintenance/write-freeze,
  and deployment identity gates.

## Unmeasured is never zero

A volume marked `TimeTracking.LEGACY_EXEMPT` predates time tracking, so its
real total is unknowable. It reports `-`, never `0m`, even when the database
holds no intervals for it. The distinction is load-bearing: rendering an
unmeasured value as `0` credits somebody with zero effort they were never
measured on.

## Hard cases carry one reason

- A hard case has a single `category` — it is one question, and the category is
  what the reader is being asked to act on.
- Blank is a real value. Cases recorded before the field existed have none, and
  the inbox lists them as **Uncategorised** rather than guessing on their
  behalf. Clicking the selected category again clears it back to blank.

## Project research extensions

- **Extensions** is the rightmost project tab for every role. Research tools use
  the shared catalog; Measurements is not a separate core tab.
- Catalog visibility never grants permissions. Backend authorization governs
  tool actions; annotators can read measurements but cannot start runs.
- Opening the catalog or Measurements mutates no annotations and queues no jobs.
  Explicit save/run controls retain their existing meaning.
- Disabling a frontend extension preserves data and jobs. Core registration,
  assignment, annotation and review remain available.

## Assistant-manager workspaces

- An assistant manager remains an annotator and appears in both People rosters.
  Grants do not replace assignments, labels or submissions.
- Only existing primary manager identities or superusers grant/revoke access;
  assistant managers cannot delegate their elevated access.
- Backend authorization validates the active workspace on every token-authenticated
  request. Annotator mode keeps ordinary annotator scope; revocation prevents
  further manager requests.
- Switching requires confirmation and reloads to Home. It never saves or submits
  pending work. Profile shows both durable roles in either workspace.
- Ordinary annotators' People/person pages omit Time report controls; automatic
  timing of their own annotation work remains available.
