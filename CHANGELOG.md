# Changelog

Notable user-facing and operational changes are recorded here. This project
follows semantic versioning for tagged releases.

## Unreleased

### Fixed

- **The manager's home fired 415 database queries to draw 35 rows.**
  `/api/submissions/` embeds each row's whole task, and the task serializer
  walks that task's submissions and every review on them — but the view never
  prefetched that chain, so it cost one query per task per relation. The
  constants `ProjectTasksView` already used were sitting in the same file.
  Measured against production data: **415 queries / 569 ms → 53 / 147 ms.**
  `MyCompletedTasksView` (the annotator's Done tab) had the same gap and the
  same fix. Both grew linearly with the queue, so the backlog was making it
  worse every day.

- **Application artifacts could land unreadable at `0600`.** See the entry
  below; the same atomic-write flaw is fixed at both sites.

- **Development ran a different application from production.** Nine
  `FEATURE_*` settings are on in production through
  `MITO_UPGRADE_PROFILE=production_integrated_v1`; development set no profile
  at all, so every one of them defaulted **off**. The browser half diverged the
  same way — `npm run build:production` enables the chunk pull queue and the
  chunk renderer, `npm run dev` did not, so local work exercised an image path
  nobody ships. Both are now aligned, and `docs/development.md` records the
  rule. Four dead flags for modules removed in September
  (`FEATURE_NOTIFICATIONS`, `FEATURE_MILESTONES`, `FEATURE_QUALITY_METRICS`,
  `FEATURE_INSTANCE_ANNOTATION`) referenced no code and are gone.

### Changed

- **First load no longer ships a 3-D engine to people reading lists.** The
  annotation canvas imports `Labels3DPanel`, which imports the whole of
  `three`, so every page paid for it. The canvas-bearing routes (viewer,
  editor, hard case, the three share pages) and the data-registration wizard
  are lazy now, and `three` is its own named chunk. **Entry bundle 1,057 kB →
  342 kB (gzip 296 kB → 104 kB).** Nothing inside the viewer changed.

- **The interface is reorganised around one primitive: a unit of work.** A task
  is a proposed change that gets submitted and reviewed, a hard case is a
  numbered, stateful, discussable thing, and a project contains both. They now
  look like it.

  - **One list, everywhere.** `WorkList` replaces four separate implementations
    of "rows of work" — the task table, the hard-case list, and the inline
    tables on the project list and the manager dashboard. Every row carries a
    state dot with an accessible label, a title, `#id`, the most recent event
    and who did it, and its person. Every list can be filtered by state,
    assignee, author, category and free text, all client-side over rows already
    fetched, with the filter held in the query string so a narrowed view is a
    link you can send.
  - **One home.** `/` is the personal home for every role, replacing the
    manager, annotator and requester dashboards. Each tab is a saved query over
    the same list, using the same calls those three pages already made. The
    manager's left "attention rail", which duplicated two of its own tabs as
    links, is gone: the count on a tab is the signal.
  - **A project is six nouns.** Overview · Data · Tasks · Cases · People ·
    Settings. "Assign" was a verb among nouns and is now a bulk action inside
    the Tasks list, working on the rows you select. "Activity" was a junk
    drawer: its hard cases became Cases, its workload table moved to Overview.
  - **A task page is a timeline with a sidebar**, and the thing you must do next
    is at the end of it. Its history is assembled from fields the task API
    already returned — no new endpoint, no new field. The canvas is a sibling
    tab rather than something further down.
  - **A hard case page is the same skeleton**: title, `#id`, state, the
    discussion with its reply box at the end, and metadata in a sidebar, with
    the canvas above.
  - **A submission is reviewed on its task's own page.** Reviewing on a separate
    route meant reading the history in one place and acting in another, then
    being redirected to a dashboard root — seven times, for a manager working
    through eight submissions. `/submissions/:id/review` still resolves; it
    redirects onto the task.
  - **Actions moved to the scope of what they change.** Page headers carry
    identity and navigation only. Edit and Share live in Settings, Delete in a
    bordered Danger zone, "Add data" is one primary button on the Data tab, and
    volume metadata is edited in place instead of behind an edit mode. `View`
    and `Annotate` remain the most prominent control on a task page.
  - **Every detail page carries a breadcrumb**, and no action redirects to a
    role home. Reviewing leaves you on the task with the result visible and the
    next waiting submission one click away.
  - The navbar is four entries — Home, Projects, People, and you. `Register
    Data` moved to the pages that own it, and Hard Cases left the top level: a
    case belongs to a project, and Home surfaces the ones that concern you.

  No model, table, column or migration changed. Everything above is presentation
  and list/detail routing over data the API already returned.

### Added

- **Hard cases carry a category.** Recording one now asks what kind of problem
  it is — uncertain, needs split, needs merge, not a mitochondrion, cut off by
  the volume boundary, or other — and the inbox filters and counts by it. The
  category is editable afterwards from the same dialog as the note, under the
  same permission.

  Blank is a real value: the 35 cases recorded before this field existed carry
  none and list as **Uncategorised**, and clicking the selected chip again
  clears it back. A reason nobody is sure of is better recorded as absent than
  guessed.

### Changed

- The login page opens on the **Annotator** tab, and Annotator is listed first.
  Development and deployed builds share one page, so both change.

### Fixed

- **18 tests left stale by `63ce567`.** That commit made two deliberate
  behaviour changes and left the tests asserting what it replaced:
  `unique_registered_image_per_dataset` (`volumes/0012`) forbids two volumes in
  one dataset sharing an `image_path`, but fifteen fixtures still reused one;
  and `_seed_working_label` now refuses an unreadable or shape-mismatched
  registered label instead of silently starting empty, which three tests still
  contradicted. Two of the three could not run at all, because `h5py` was
  absent from the environment despite being declared in `environment.yml` —
  leaving the HDF5 and NIfTI read paths unverified while the suite looked green.

- **The viewer's layer field could silently discard a typed layer.**
  `CommitNumberInput` kept a local draft so a controlled re-render could not
  fight the user mid-edit, but re-synced it from the prop unconditionally — so
  a background update landing between a keystroke and Enter/blur replaced what
  had been typed, and the commit applied the old number. The draft is now held
  while the field has focus.

- **The frontend suite is no longer load-dependent.** `scrubBenchmark.test.ts`
  asserted wall-clock p95 from inside the ordinary suite (now behind
  `MITO_RUN_BENCH`, reachable via `npm run bench:phase13`); vitest's 5 s
  `testTimeout` collided with Testing Library's async budget, masking what a
  slow wait was waiting for (now 20 s against 5 s); and one test synchronised
  on the slice image before asserting on the layer field, which updates a
  commit later.

### Decided

- **The two label-seeding policies diverge on purpose.** `_seed_working_label`
  refuses an unreadable registered label; `_load_or_init_label` starts empty.
  That is safe because of call order: the strict one is the editor's entry
  point, the lenient one runs only from SAM2 tracking, and tracking needs
  prompts which need the editor. Both halves are documented in place and locked
  by `annotation/test_seeding_policy.py`.

## 1.1.5

- Current production release baseline.

## Unreleased

### Added

- **Per-instance annotation.** `annotation.LabelInstanceAnnotation` records a
  mitochondrion's morphology (normal, elongated, fragmented, swollen, donut,
  mega, cristae loss, mitophagy) and independent QA flags (uncertain, boundary
  truncated, needs split, needs merge, false positive) for one label id in one
  volume, with an Instance details panel in the Annotate view. Purely additive:
  an absent row means "not annotated", so every existing volume is valid with
  no rows, and a row that would carry nothing is deleted rather than stored.
  Behind `FEATURE_INSTANCE_ANNOTATION`.
- **Inbox.** `accounts.Notification` plus a navbar bell and `/inbox`. Written
  by the service layer on assignment, submission, review decision, hard-case
  reply, and quality flag; nobody is notified about their own action. The
  `FEATURE_NOTIFICATIONS` flag gates the read endpoints only — recording
  continues while it is off, so enabling it later reveals the accumulated
  history rather than an empty inbox.
- **Milestones and delivery analytics.** `projects.Milestone` with dated
  targets, plus burndown, throughput, per-annotator productivity, and an
  attention queue on a new project **Delivery** tab. Progress is recomputed
  from tasks on every read and never stored. Behind `FEATURE_MILESTONES`.
- **Measured quality.** `annotation.QualityScore` records Dice, IoU, precision,
  recall, instance F1, false merges, false splits, and variation of information
  between a submission and a trusted reference. Two sources, neither of which
  costs extra annotation: a gold-standard volume scored against an
  already-approved submission, and the difference between what an annotator
  submitted and what a reviewer actually approved. Surfaced on the review page,
  a person's page, and a project **Quality** tab. Behind
  `FEATURE_QUALITY_METRICS`.

### Changed

- The login page now opens on the **Annotator** tab, and Annotator is listed
  first. Development and deployed builds share one page, so both change.
- `AnnotatorProfile.quality_score` is now derived from measured gold-standard
  scores instead of being a permanent `0.0`. A person with no scored
  submissions reports "not measured", never zero.

### Added (second pass)

- **The remaining notification verbs now have emission points.** `TASK_WITHDRAWN`
  fires from `withdraw_project_assignments`, `HARD_CASE_OPENED` / `HARD_CASE_REPLIED`
  from hard-case creation and replies, and `DEADLINE_APPROACHING` /
  `MILESTONE_AT_RISK` from a new `notify_deadlines` management command. All five
  were defined and documented but written by nothing; a test now asserts that
  every verb in the vocabulary is reachable.
- **`prune_notifications`** — deletes read notifications past a retention
  window. Unread rows are never deleted however old, because they are still
  owed to somebody.
- **`GET /api/delivery/`** — the throughput, productivity, and attention panels
  across every project, and an **Attention** tab on the manager dashboard that
  reads it. `core.statistics` already accepted `project=None`; nothing exposed
  it, so finding the overdue project meant opening each one in turn.

### Added (third pass)

- **Gold-standard configuration UI.** A manager-only card on the volume page
  marks a volume as a known test and picks the approved submission it is scored
  against, backed by a new `GET /api/volumes/<id>/approved-submissions/`. The
  backend switch existed but nothing called it, so the whole gold-standard half
  of the quality module was unreachable outside `curl`. The card is hidden
  entirely from non-managers — an annotator who could see it would know they
  were being tested, which is the one thing a gold standard must not reveal.
- **Milestones can be edited.** Inline name / due date / target editing on each
  row; previously they could only be created and deleted. Removal now asks for
  confirmation first.

### Fixed (found by enabling the flags)

- **One test depended on the host's `.env`.**
  `accounts.test_notifications` asserted the disabled-API path by relying on
  `FEATURE_NOTIFICATIONS` defaulting to off, so it began failing the moment the
  flag was enabled in a developer's environment. It now pins the flag
  explicitly and passes with the flags both on and off. The full suite was run
  in both configurations to confirm nothing else was ambient.

### Decided

- **The two label-seeding policies diverge on purpose.** `_seed_working_label`
  refuses an unreadable registered label; `_load_or_init_label` starts empty.
  That is safe because of call order, not coincidence: the strict one is the
  editor's entry point, the lenient one runs only from SAM2 tracking, and
  tracking needs prompts which need the editor — so a corrupt label is stopped
  before the lenient branch can be reached with no working copy. Both halves
  are now documented in place and locked by `annotation/test_seeding_policy.py`,
  so the divergence is a recorded decision rather than an open question.

### Fixed

- **18 stale tests from `63ce567`.** That commit made two deliberate behaviour
  changes and left the tests asserting what it replaced:
  - `unique_registered_image_per_dataset` (`volumes/0012`) forbids two volumes
    in one dataset sharing an `image_path`. Fifteen fixtures across
    `volumes.test_region_pyramid`, `test_pyramid_build`, and
    `test_chunk_service` still created a second volume reusing the first's
    path. Each now registers its own image, as production does.
  - `_seed_working_label` now refuses an unreadable or shape-mismatched
    registered label instead of silently starting an empty working copy. Three
    tests still asserted the old lenient behaviour and now assert the refusal,
    with the reasoning recorded: a silent empty seed on a proofreading volume
    hands the annotator a blank mask where a prediction should have been.

  Two of the three policy tests live in `annotation.test_hdf5_source` and could
  not run at all, because `h5py` was absent from the environment despite being
  declared in `environment.yml`. The other sixteen failed on PostgreSQL as well
  as sqlite.

- **The viewer's layer field could silently discard a typed layer.**
  `CommitNumberInput` keeps a local draft so a controlled re-render cannot
  fight the user mid-edit, but it re-synced that draft from the prop
  unconditionally — so a background update landing between a keystroke and
  Enter/blur replaced what had been typed, and the commit applied the old
  number. The draft is now held while the field has focus and reconciled on
  blur. Rare by hand, reliable under load, and it is what made one viewer test
  flaky.

- **The frontend suite is no longer load-dependent.** Three causes:
  `scrubBenchmark.test.ts` asserted wall-clock p95 against a fixed 100 ms gate
  from inside the ordinary suite (now behind `MITO_RUN_BENCH`, reachable via
  `npm run bench:phase13`); vitest's 5 s `testTimeout` collided with Testing
  Library's async budget so a slow wait was killed before it could report what
  it was waiting for (now 20 s against 5 s); and one test synchronised on the
  slice image before asserting on the layer field, which updates a commit
  later. Measured 0 failures in 12 consecutive full runs, against a
  reproducible failure before.

### Notes

- `docs/development.md` now records two traps this uncovered: Django test
  discovery from the repository root finds **zero** tests and exits `0`
  (indistinguishable from a pass), and an environment missing `h5py`/`nibabel`
  silently leaves the HDF5 and NIfTI read paths unverified.
- All four features default to **off**; an existing deployment upgrades to
  identical behaviour. Every new route is registered unconditionally and
  returns `503` when its flag is off, so a misconfiguration is distinguishable
  from a routing typo.
- All migrations are additive — every new column is nullable or defaulted, no
  existing column is altered or dropped, and no data is rewritten.
- New invariant documented in `docs/product-invariants.md`: **unmeasured is
  never zero.** Unknown quality metrics, unknowable legacy annotation time, and
  unclassified instance morphology all render as an em-dash, never as `0`.
