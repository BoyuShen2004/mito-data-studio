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

## Unmeasured is never zero

Several figures in this application are genuinely *unknown* for some rows, and
the difference between "we did not measure this" and "we measured it as zero"
is load-bearing. Rendering an unmeasured value as `0` accuses somebody of work
they were never assessed on.

- **Annotated time.** A volume marked `TimeTracking.LEGACY_EXEMPT` predates
  time tracking, so its real total is unknowable. It reports `-`, never `0m`,
  even when the database holds no intervals for it.
- **Quality scores.** Every metric on `annotation.QualityScore` is nullable.
  A provider that cannot compute a figure records its absence. An annotator
  with no scored gold-standard submissions reports `None`, not `0.0`, and the
  UI renders that as an em-dash.
- **Comparison failures.** A shape mismatch between a submission and its
  reference writes **no score at all** rather than a zero one — the
  disagreement is about configuration, not about annotation quality.
- **Instance morphology.** A blank `LabelInstanceAnnotation.morphology` means
  nobody has classified that instance. It is deliberately distinct from
  `MitoMorphology.NORMAL`, which is the positive claim that somebody looked and
  found nothing unusual. Project summaries report the two separately.
- **Milestone targets.** A milestone whose `target_value` is zero reports
  `percent_complete: null`, so "no target set" never renders as "target met".

## Instance annotation is additive

- An **absent** `LabelInstanceAnnotation` row means "not annotated". Every
  volume registered before the feature existed is valid with zero rows, and
  nothing backfills the table.
- A row that would carry no information is **deleted** rather than stored, so
  "has a row" always means "somebody recorded something".
- Instance annotation adds a side panel only. It does **not** change how the
  canvas colours a label: `labelColor.ts` still derives every instance colour
  from the id hash, and the backend mirror in
  `annotation/visualization/slice_io.py` is untouched. Any future change that
  tints by morphology must keep an unannotated instance rendering byte-
  identically to today, and must carry a regression test that says so.

## Notifications record regardless of the API flag

`FEATURE_NOTIFICATIONS` gates the inbox **endpoints**, not the recording. The
service layer writes notifications whether or not the flag is on, so a
deployment that enables the inbox later shows the history that accumulated
rather than starting from empty.
