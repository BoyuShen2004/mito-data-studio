# Final-release WIP audit (2026-07-31)

Baseline: `bdf5e73e052ff154cf2418d2c4a98731769ee283` on
`webknossos-transformation`. The frozen 78-path inventory is stored outside
the repository at
`/home/weidf/shenb/mito-release-prep-20260731T180819Z/wip-backup/`.

The audit read the actual patch for every tracked path and the full contents of
every untracked path. The principal provenance is the implemented product
brief `progress/history/05-submit-people-hardcases.md`; its backend half was
preserved by `e5ad279`, and the initial collaboration frontend by `9e458f5`.
The remaining working-tree frontend is the more complete UI for those already
committed contracts. It is not generated output.

## Application and UI paths (A-C)

Unless a row says otherwise, dependencies are the committed submit/review,
People, project-membership and HardCase APIs; backend coverage is in
`annotation/test_submit_loop.py`, `annotation/test_tracking.py`,
`annotation/test_review_loop.py`, and `accounts/test_people.py`. Mounted
frontend coverage is in `pages/collaborationWorkflow.test.tsx`. Omitting an A
or B row would leave a committed backend feature unreachable or enforce the
wrong client-side permission. Group `collaboration` is committed together;
group `workflow-ui` is a separate presentation/workflow commit.

| Category | Path | Intended behavior / provenance | Equivalent already committed? | Extra dependencies/tests | Risk if omitted | Commit group |
|---|---|---|---|---|---|---|
| A | `frontend/src/api/people.ts` | Role-scoped People overview, person detail and self-profile transport; brief E | API exists, but this documented client surface is fuller | `accounts/api.py`; People backend tests | People/profile UI cannot load reliably | collaboration |
| A | `frontend/src/api/submissions.ts` | Send `allow_further_annotation` on review; brief C | Backend contract only | submit-loop tests + mounted approve test | Manager cannot intentionally keep approved work open | collaboration |
| A | `frontend/src/api/tasks.ts` | Manager task lock/reopen transport | Backend endpoint only | submit-loop tests | Approved work cannot be reopened from UI | collaboration |
| A | `frontend/src/api/viewer.ts` | Remove superseded share-creation client and keep project HardCase/public adapters aligned | New HardCase API is committed; old helper is obsolete | public share backend/frontend tests | Two conflicting hard-case creation contracts | collaboration |
| A | `frontend/src/auth/AuthContext.tsx` | Refresh navbar/user after profile edit | No equivalent refresh hook | mounted People flow via typecheck/build | Stale identity after profile save | collaboration |
| A | `frontend/src/components/HardCaseList.tsx` | Permission-aware newest-first project/inbox list with resolve/reopen | Simpler committed list exists | HardCase API tests | Current project workflow and audit trail are degraded | collaboration |
| A | `frontend/src/pages/HardCaseDetailPage.tsx` | Reuse AnnotationCanvas with server-provided edit/takedown permission | Simpler committed page exists | mounted edit-gate test | Wrong editing affordance or loss of project workflow | collaboration |
| A | `frontend/src/pages/HardCasesPage.tsx` | Open/resolved inbox and explicit historical toggle | Simpler committed page exists | HardCase API tests | Resolved cases disappear from usable workflow | collaboration |
| A | `frontend/src/pages/PeoplePage.tsx` | Role-specific collaboration panels and editable profile | Simpler committed page exists | People backend tests | Major user-facing People capability is incomplete | collaboration |
| A | `frontend/src/pages/PersonPage.tsx` | Shareable read-only person/project/workload view | Simpler committed page exists | People backend tests | Person links lose useful content | collaboration |
| A | `frontend/src/pages/ProjectDetailPage.tsx` | Project Hard Cases plus complete dataset/assignment surface | Hard Cases not otherwise on project page | project membership + list API | Brief F acceptance is not met | collaboration |
| A | `frontend/src/pages/ReviewSubmissionPage.tsx` | Latest-round evidence and approve-close/keep-open decision | Backend is committed; UI is not | mounted approval transport test | Approval silently always uses default lock | collaboration |
| A | `frontend/src/pages/TaskDetailPage.tsx` | API-driven annotate/submit gates and review-round state | Backend gates committed; old page re-derived behavior | submit-loop tests | Submit/annotate can disappear or disagree with API | collaboration |
| A | `frontend/src/pages/ViewerPage.tsx` | Full volume canvas; stable editor through repeat submit; task sharing | Existing committed page lacks complete workflow | mounted stable-canvas/lock tests; Phase 14 canvas | Silent loss of in-memory edit history on submit or missing volume view | collaboration |
| A | `frontend/src/pages/VolumeDetailPage.tsx` | Unified volume/task page, manager lock toggle, assignment editing | Older split pages are committed | volume/task APIs; submit-loop tests | Managers lose core task administration | collaboration |
| A | `frontend/src/routes/AppRoutes.tsx` | Authenticated People/Hard Cases placement and full-bleed case canvas | Routes exist in simpler form; ordering/commentary is complementary | route roles + backend permission gates | User-facing features become unreachable or mis-gated | collaboration |
| A | `frontend/src/types/people.ts` | Complete role-projected People contract | Partial committed types | People serializers/tests | Runtime fields are unrepresented and easy to misuse | collaboration |
| A | `frontend/src/types/task.ts` | Server gates, lock and review-round contract | Backend serializer is committed | submit-loop tests | Client re-derives stale task rules | collaboration |
| B | `frontend/src/types/index.ts` | New writes accept only none/partial/prediction; legacy proofread stays backend-readable | Backend `WRITABLE_LABEL_TYPES` is stronger and authoritative | volume service tests | UI can offer a value backend correctly rejects | workflow-ui |
| B | `frontend/src/pages/RegisterDataPage.tsx` | Enforce mask↔label-type invariant before registration | Backend validation exists | volume/data-registration tests | Avoidable 400s and inconsistent registration UX | workflow-ui |
| B | `frontend/src/components/TaskTable.tsx` | Use `can_annotate`, display lock, link managers to merged volume page | Backend gate exists, old table ignores it | mounted lock test indirectly; typecheck | Locked tasks still advertise editing | workflow-ui |
| B | `frontend/src/pages/AnnotatorDashboard.tsx` | Rejected tasks remain active work; submitted/approved are history | Backend list split is committed | review-loop tests | Returned work is hidden from annotator | workflow-ui |
| C | `frontend/src/components/AssignmentPlanEditor.tsx` | Stable toolbar/status layout and explicit volume/label/frame columns | No exact equivalent | assignment API; frontend build | Layout shifts and ambiguous planning table | workflow-ui |
| C | `frontend/src/components/DatasetsCard.tsx` | One coherent dataset card with registration CTA and clearer volume table | No exact equivalent | project ID and existing CSS | Fragmented project workflow | workflow-ui |
| C | `frontend/src/components/MetadataCard.tsx` | Suppress empty metadata cards | No equivalent | none | Empty visual noise | workflow-ui |
| C | `frontend/src/components/Navbar.tsx` | Explain and retain universal People/Hard Cases navigation | Links already exist; comment is complementary | role-scoped APIs | Low; helps prevent later role-gating regression | workflow-ui |
| C | `frontend/src/features/proofreading/ProofreadingLaunch.tsx` | Avoid duplicate in-app launch; retain external/export actions | No exact equivalent | proofreading provider API | Duplicate/confusing editor actions | workflow-ui |
| C | `frontend/src/features/viewer/Labels3DPanel.tsx` | Use deterministic automatic Z preview scaling and remove manual preview-only modes | Auto mode already existed; this makes it exclusive | 3-D mesh path | Extra controls can create inconsistent previews, but no data risk | workflow-ui |
| C | `frontend/src/pages/LoginPage.tsx` | Demo chips include two requesters and remain opt-in in production; reset stays DEBUG-only | Seeded requesters are committed | `VITE_SHOW_DEMO_ACCOUNTS`; seed tests | Demo/release validation cannot exercise requester role cleanly | workflow-ui |
| C | `frontend/src/pages/ManagerDashboard.tsx` | Remove redundant explanatory copy | No behavioral equivalent needed | none | Low | workflow-ui |
| C | `frontend/src/pages/NewProjectPage.tsx` | Concise project creation copy/placeholders | No behavioral equivalent needed | none | Low | workflow-ui |
| C | `frontend/src/pages/RegisterDataPage.tsx` | Also simplifies path hints and three-tier layer copy | No exact equivalent | registration UI | Low-to-medium usability | workflow-ui |
| C | `frontend/src/routes/backNavigation.ts` | Correct nested People/HardCase/volume fallbacks | Older fallback is incomplete | router | Back button lands on wrong role home | workflow-ui |

## Documentation paths (D)

These describe behavior already present in committed backend code and in the
frontend accepted above. Omitting them would leave the operations/API map
factually stale. They are committed as `collaboration-docs`.

| Category | Path | Purpose |
|---|---|---|
| D | `README.md` | Declared environment update, demo build flag, production-safe demo/reset behavior |
| D | `progress/PROJECT.md` | Latest-only submission and project HardCase domain relationships |
| D | `progress/README.md` | Current implemented brief and module index |
| D | `progress/api.md` | People, lock/review loop and HardCase endpoint contracts |
| D | `progress/backend/accounts/MODULE.md` | People derivation/profile ownership |
| D | `progress/backend/annotation/MODULE.md` | Submit, lock, HardCase and permission behavior |
| D | `progress/codemap.md` | Navigation pointers for collaboration code |
| D | `progress/development.md` | Reproducible environment/provider setup |
| D | `progress/frontend/api/MODULE.md` | Client/auth contracts |
| D | `progress/frontend/components/MODULE.md` | Shared table/card responsibilities |
| D | `progress/frontend/features/MODULE.md` | Viewer/proofreading ownership |
| D | `progress/frontend/pages/MODULE.md` | People, Hard Cases and merged detail workflows |
| D | `progress/frontend/routes/MODULE.md` | Role and nested-route contract |
| D | `progress/history/README.md` | History index entry |
| D | `progress/history/05-submit-people-hardcases.md` | Authoritative product brief, acceptance, implementation notes and test provenance |

## Research/historical evidence (E)

The following 31 paths are authoritative design/research evidence used by
Phases 0-14, but are not imported by runtime code. They are preserved in Git
as one documentation archive because committed ADRs and phase records refer to
them. They do not enter application bundles or deployment configuration.

| Category | Path |
|---|---|
| E | `docs/webknossos-transformation/00-repository-verification.md` |
| E | `docs/webknossos-transformation/01-authoritative-source-index.md` |
| E | `docs/webknossos-transformation/02-license-and-attribution-analysis.md` |
| E | `docs/webknossos-transformation/03-webknossos-product-model.md` |
| E | `docs/webknossos-transformation/04-webknossos-task-management-analysis.md` |
| E | `docs/webknossos-transformation/05-webknossos-assignment-engine.md` |
| E | `docs/webknossos-transformation/06-webknossos-annotation-analysis.md` |
| E | `docs/webknossos-transformation/07-webknossos-interpolation-analysis.md` |
| E | `docs/webknossos-transformation/08-webknossos-viewer-architecture.md` |
| E | `docs/webknossos-transformation/09-webknossos-datastore-architecture.md` |
| E | `docs/webknossos-transformation/10-webknossos-performance-and-stability.md` |
| E | `docs/webknossos-transformation/11-webknossos-ai-integration.md` |
| E | `docs/webknossos-transformation/12-mito-data-studio-codebase-audit.md` |
| E | `docs/webknossos-transformation/13-mito-data-studio-workflow-audit.md` |
| E | `docs/webknossos-transformation/14-complete-feature-gap-matrix.md` |
| E | `docs/webknossos-transformation/15-target-product-definition.md` |
| E | `docs/webknossos-transformation/16-target-domain-model.md` |
| E | `docs/webknossos-transformation/17-target-task-management-design.md` |
| E | `docs/webknossos-transformation/18-auto-fill-scheduler-design.md` |
| E | `docs/webknossos-transformation/19-target-annotation-design.md` |
| E | `docs/webknossos-transformation/20-target-volume-infrastructure.md` |
| E | `docs/webknossos-transformation/21-target-rendering-architecture.md` |
| E | `docs/webknossos-transformation/22-target-persistence-and-recovery.md` |
| E | `docs/webknossos-transformation/23-target-observability-design.md` |
| E | `docs/webknossos-transformation/24-migration-and-rollout-strategy.md` |
| E | `docs/webknossos-transformation/25-testing-benchmark-and-soak-strategy.md` |
| E | `docs/webknossos-transformation/26-license-compliance-plan.md` |
| E | `docs/webknossos-transformation/27-claude-code-phase-map.md` |
| E | `docs/webknossos-transformation/CLAUDE_CODE_MASTER_PROMPT.md` |
| E | `docs/webknossos-transformation/README.md` |
| E | `docs/webknossos-transformation/evidence/source-map.md` |

## Empty categories and resolution

- F (generated/runtime/build): none of the frozen 78 paths.
- G (obsolete/superseded): none. Small formatting-only hunks are kept with
  their containing valid workflow rather than deleted from a mixed file.
- H (user decision required): none after comparing the brief, backend choices,
  current API tests and Phase 0-14 behavior. In particular, automatic-only 3-D
  Z preview scaling is explicitly preview behavior and cannot alter data.

All 78 original paths therefore have a release disposition. The added mounted
test and this audit are new release-preparation files, not members of the
frozen inventory.
