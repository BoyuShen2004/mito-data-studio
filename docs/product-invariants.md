# Product invariants

These behaviors are intentional product requirements. Do not remove, hide, or
reinterpret them during UI cleanup, authentication refactors, or release work
without explicit user approval and updated regression tests.

## Development accounts on the login page

- The **Development accounts** section appears directly below “Need an
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
