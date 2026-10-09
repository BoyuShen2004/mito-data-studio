# Documentation audit — 2026-10-09

## Follow-up: project Extensions

The subsequent Extensions change supersedes references in this dated audit to
a standalone Measurements tab. The current route is **Project → Extensions →
Measurements**; legacy `tab=measurements` links retain their volume selection.
Measurement APIs, source rules and scientific limits are unchanged. See the
[current user guide](../user-guide/extensions.md) and
[extension contract](../engineering/extensions.md). The historical findings below
remain evidence for their audited baseline.

## Baseline and method

Audit baseline: `origin/main` and `HEAD` both `1c47b3bce8e032957687aba9ec658e026eae1ac7`, verified after `git fetch origin main`. The checkout branch is `feature/measure-mito`; its files match main. Initial worktree was clean. This report was completed before editing other documentation. Approximate locations below describe the pre-fix documents; function/test identifiers are preferred over unstable line numbers.

Read root policies, maintained `docs/` onboarding, user, engineering, operations, release and research pages, and legacy forwarding pages. Compared claims with runtime code, API authorization, configuration and regression tests. Historical deployment records remain dated evidence, not fresh operational verification. This is a documentation audit, not a certification of security, licensing compliance or biological accuracy.

## Severity totals

| Severity | Issues | Meaning |
| --- | ---: | --- |
| P0 | 6 | Data persistence, destructive editing or scientific source meaning |
| P1 | 8 | Material user workflow or permissions |
| P2 | 8 | Architecture, deployment, formats, provenance or cleanup |
| P3 | 3 | Terminology and detail |

These are 25 distinct issue groups; repeated occurrences are not counted separately. VERIFIED claims are recorded below rather than counted as issues. Classification and counts remain the pre-fix findings, even after remediation.

## P0 findings

### A01 — INCORRECT

- Documentation: docs/user-guide.md; docs/user-guide/05-annotation-tools.md; docs/operations/production-host.md; docs/research/methods.md.
- Section / approximate location: Core mental model / Draft / Data-safety rules / Annotation workflow.
- Claim: Only explicit Save persists browser edits.
- Implementation: `frontend/src/features/viewer/AnnotationCanvas.tsx: saveLabels and flushSafeDraft (around 2463–2690)`.
- Relevant tests: pendingSliceBuffer.test.ts; AnnotationCanvasOutsideEdits.test.tsx cover saves/conflicts; no dedicated autosave regression found.
- Recommended change: Describe 30-second and hidden-tab best-effort autosave, ROI exclusion, failure handling, and explicit Save before Submit. Do not promise confirmation is a persistence barrier.
- Confidence: High.

### A02 — INCORRECT

- Documentation: docs/overview.md; docs/getting-started/README.md; docs/getting-started/glossary.md; docs/operations/production-host.md.
- Section / approximate location: Purpose / data flow / Official label / Data-safety rules.
- Claim: Only approval changes the official label.
- Implementation: `backend/annotation/services.py: withdraw_project_assignments, promote_working_label_to_official; backend/accounts/teams.py`.
- Relevant tests: backend/accounts/test_collaboration_api.py: test_delete_working_team_promotes_mask_and_records_cancelled_done_item; test_admin_collaboration.py.
- Recommended change: Explain withdrawal promotion, pending-channel retirement and task unassignment; official source can be a saved draft and is not proof of approval.
- Confidence: High.

### A03 — INCORRECT

- Documentation: docs/user-guide/05-annotation-tools.md; docs/user-guide/workflows.md; docs/overview.md; docs/research/methods.md.
- Section / approximate location: Active label and overwrite policy / tool table / Annotation.
- Claim: Empty-only protects existing voxels for all painting and tools; brush has circular/square footprint.
- Implementation: `frontend/src/features/viewer/AnnotationCanvas.tsx: paintAt, commitAiPreview, applyBoxErase; annotate/AnnotateToolChrome.tsx`.
- Relevant tests: AnnotationCanvasOutsideEdits.test.tsx protects hidden/verified IDs; localFloodFill.test.ts tests overwrite policy.
- Recommended change: Scope empty/all policies to Interpolate, Flood fill, Track and outside-ROI projection. Brush and committed masks replace unprotected IDs; erase clears unprotected pixels. Brush footprint is circular; cursor styles are display preferences.
- Confidence: High.

### A04 — PARTIALLY ACCURATE

- Documentation: docs/user-guide/06-assisted-and-track.md; docs/user-guide/workflows.md.
- Section / approximate location: Region-only mode.
- Claim: Outside content is protected from ordinary edits; protected changes may be omitted on Save.
- Implementation: `frontend/src/features/viewer/outsideRegionEdits.ts; AnnotationCanvas.tsx: saveLabels; backend/annotation/region_mask.py`.
- Relevant tests: AnnotationCanvasOutsideEdits.test.tsx: stages paint, strict visibility, projection, Save confirmation and ROI split tests; backend/annotation/test_region_mask_roi.py.
- Recommended change: Explain whole-instance visibility, hidden/verified protection, staged outside work, toggle projection, and confirmed inside-only Save discarding omitted outside edits. Split/watershed plans preserve their outside-ROI changes.
- Confidence: High.

### A25 — INCORRECT

- Documentation: docs/user-guide.md; docs/engineering/architecture.md; docs/overview.md.
- Section / approximate location: Core mental model / Integrity / sharing guarantees.
- Claim: All public links can be revoked.
- Implementation: `backend/annotation/task_sharing.py`: signing.loads has no max_age or revocation record; `TaskPublicShareView` permits any authenticated task viewer to mint a task-scoped token. Legacy `/share/task/:token` remains routed.
- Relevant tests: `backend/annotation/test_tracking.py`: test_full_task_share_is_public_read_only_and_permission_scoped; `frontend/src/pages/TaskSharePage.test.tsx`. No expiration/revocation test exists for this stateless path.
- Recommended change: Qualify revocability to database-backed shares/hard cases; document legacy signed task tokens have no per-link Stop or age expiry. Prefer revocable volume shares when revocation is required.
- Confidence: High (code); legacy path retained even though current sharing UI uses database-backed scopes.

### A18 — PARTIALLY ACCURATE

- Documentation: docs/engineering/data-and-storage.md; docs/user-guide/02-projects-and-data.md; docs/research/methods.md; docs/research/claims-matrix.md.
- Section / approximate location: Owned artifacts / Deleting data / Quality controls.
- Claim: Deletion removes every generated artifact and never any registered label path.
- Implementation: `backend/projects/services.py: _generated_paths, _plan_file_cleanup; processing/models.py: SET_NULL links`.
- Relevant tests: backend/projects/tests.py: generated cleanup/source/symlink/sibling tests; no processing job artifact cleanup test or approval-to-historical-source deletion regression found.
- Recommended change: Own generated approved labels may be removed even if current label_path points there. Generic processing_jobs outputs/history remain; do not promise total cleanup. Protection tracks current registered paths, not all historical imports. Approval clears the previous label reference; an old label inside an owned folder may then be deleted by dataset/project tree cleanup (B08). Qualify source protection and recommend independent source archives.
- Confidence: High for current references and retained job artifacts; moderate for historical-label loss (code path inspected, end-to-end case not executed).

## P1 findings

### A05 — INCORRECT

- Documentation: docs/user-guide/06-assisted-and-track.md; docs/engineering/ai-and-algorithms.md; docs/operations/docker.md; docs/operations/reference-hardware.md; docs/research/methods.md.
- Section / approximate location: SAM2 runtime / Build profiles.
- Claim: SAM2 works on CPU when CUDA is absent; ai-cpu is slow but complete.
- Implementation: `backend/annotation/tracking/adapters/sam2.py: _load; cellable_port/ai/registry.py: _load_mask_model; tracking/registry.py`.
- Relevant tests: cellable_port/ai/test_mask_backend.py: load failure/no CUDA; test_tracking.py has lower-level CPU wrapper coverage only.
- Recommended change: SAM2 application provider requires CUDA; ai-cpu installs CPU torch but cannot enable these tools. Distinguish CPU local stand-in tracking (including missing-torch fallback) from SAM2.
- Confidence: High.

### A06 — PARTIALLY ACCURATE

- Documentation: docs/user-guide/08-collaboration-and-safety.md; README.md.
- Section / approximate location: Public sharing / Roles.
- Claim: Managers manage public shares; stopping parent sharing revokes the live access.
- Implementation: `backend/projects/share_api.py: PublicShareAdminView, PublicShareRevokeView, share_tree`.
- Relevant tests: backend/projects/test_public_shares.py: accessible-volume sharing, nonmanager scope denial, direct-only parent stop.
- Recommended change: Any authenticated volume viewer can open a volume share; only its creator or a manager can revoke it. Parent Stop revokes direct shares only; child tokens remain live.
- Confidence: High.

### A07 — PARTIALLY ACCURATE

- Documentation: docs/user-guide/07-submit-and-review.md.
- Section / approximate location: Two submission channels / Revision loop.
- Claim: Revision or rejection returns the task for more work.
- Implementation: `backend/annotation/services.py: _return_to_annotator, _retire_submissions, approve_submission`.
- Relevant tests: backend/annotation/test_dual_submission_channels.py: test_reject_online_leaves_offline_pending; test_review_loop.py.
- Recommended change: Resubmit voids same-channel pending only; revision/reject leaves sibling pending and task submitted; stale review decisions fail before promotion.
- Confidence: High.

### A08 — PARTIALLY ACCURATE

- Documentation: docs/user-guide/05-annotation-tools.md.
- Section / approximate location: Tool reference: Split.
- Claim: Split separates components into IDs without describing removal.
- Implementation: `backend/annotation/cellable_port/split_components.py; services.py: plan_split_components_task`.
- Relevant tests: backend/annotation/test_cellable_port.py; test_whole_volume_ops_api.py: SplitComponentsReturnsPendingPlan.
- Recommended change: State 26-connectivity, default 100-voxel threshold, small components cleared (all may disappear), largest surviving component keeps parent ID, bounded crop failure and pending-plan Undo.
- Confidence: High.

### A09 — PARTIALLY ACCURATE

- Documentation: docs/user-guide/06-assisted-and-track.md.
- Section / approximate location: Track workflow / Save progress.
- Claim: Queueing uses the active ID when appropriate; Confirm turns preview into pending edits.
- Implementation: `frontend/src/features/viewer/AnnotationCanvas.tsx: queueActiveTrackingPrompt, propagateTrackingQueue, saveTrackProgress, reviewLocalTrackPreview`.
- Relevant tests: AnnotationCanvasTrackSeeds.test.tsx; AnnotationCanvasTrackReview.test.tsx; annotate/TrackRail.test.tsx.
- Recommended change: Queue always mints fresh ID; seed/range writes are durable separately; range expands with seed layer. Propagate already applies pending planes; Confirm retires parents; Reject restores before planes as undoable pending edit. Flag autosave interaction separately.
- Confidence: High.

### A10 — UNDOCUMENTED IMPLEMENTED BEHAVIOR

- Documentation: docs/user-guide/05-annotation-tools.md; docs/user-guide/07-submit-and-review.md.
- Section / approximate location: Keyboard shortcuts / label lifecycle.
- Claim: Verify is listed without its saved-label requirement or distinction from approval.
- Implementation: `backend/annotation/services.py: set_label_lifecycle_action; frontend AnnotationCanvas.tsx: handleLifecycleAction`.
- Relevant tests: backend/annotation/test_whole_volume_ops_api.py: verified target / poisoned pending; AnnotationCanvasOutsideEdits.test.tsx: verified lock conflict.
- Recommended change: Explain Verify/Unverify protection and immediate sidecar persistence; Verify first flushes all pending planes; verification is not manager approval.
- Confidence: High.

### A11 — INCORRECT

- Documentation: docs/user-guide/08-collaboration-and-safety.md.
- Section / approximate location: Safety checklist / Hard cases.
- Claim: Errors appear only in one browser popup; hard cases have an optional note but no category workflow.
- Implementation: `frontend/src/components/MitoMeasurements.tsx; ProjectMeasurements.tsx; backend/annotation/services.py: normalize_hard_case_category, can_take_down_hard_case`.
- Relevant tests: MitoMeasurements.test.tsx; backend/annotation/test_hard_case_category.py; docs/product-invariants.md.
- Recommended change: Document component-specific inline errors, one optional category, creator/manager changes, blank Uncategorised, resolve distinct from revoke.
- Confidence: High.

### A12 — STALE

- Documentation: docs/user-guide/02-projects-and-data.md; docs/user-guide/08-collaboration-and-safety.md.
- Section / approximate location: Project tab table / guide navigation.
- Claim: Project has six tabs.
- Implementation: `frontend/src/pages/ProjectDetailPage.tsx: tabs; components/ProjectMeasurements.tsx`.
- Relevant tests: frontend/src/pages/ProjectDetailPage.test.tsx; components/ProjectMeasurements.test.tsx.
- Recommended change: Add Measurements tab with member read/export, manager run and voxel-size save; link chapter 8 onward to Measurements.
- Confidence: High.

## P2 findings

### A13 — PARTIALLY ACCURATE

- Documentation: docs/engineering/architecture.md; docs/engineering/ai-and-algorithms.md.
- Section / approximate location: Background processing / diagram / Deterministic algorithms.
- Claim: Generic queued heavy jobs are handled by local adapter or SLURM; all job type vocabulary can imply real pipelines.
- Implementation: `backend/processing/{models,services,registry,interfaces}.py; adapters/local.py; core/choices.py; volumes/pyramid/jobs.py; annotation/measurement_api.py`.
- Relevant tests: backend/processing/tests.py; volumes/test_pyramid_jobs.py; annotation/test_measurement_api.py.
- Recommended change: Qualify SPA token authentication vs optional session CSRF and explicit reset CSRF; document ProcessingJob metadata, actual pyramid/measurement producers, inline dispatcher-native runners, mock no-argv result, allowlist basename, environment, timeout, manifests, polling/cancellation/retry limitations and empty lifecycle hook. Distinguish browser-local flood fill/interpolation from server plan/apply APIs; browser interpolation does not use physical spacing.
- Confidence: High.

### A14 — UNDOCUMENTED IMPLEMENTED BEHAVIOR

- Documentation: docs/engineering/architecture.md.
- Section / approximate location: Background processing.
- Claim: SLURM integration is named without its operational limits.
- Implementation: `backend/processing/adapters/slurm.py; interfaces.py; services.py`.
- Relevant tests: processing/tests.py: test_slurm_builds_private_script_from_argv; no real scheduler/poll/cancel integration tests found.
- Recommended change: Document sbatch script/command-as-script-path/argv routing, partition/account, sacct-only polling, scancel, exact state mapping, unknown-state preservation, shared storage and no automatic output collection/scientific implementation. SQUEUE setting is unused.
- Confidence: High (code), limited runtime evidence.

### A15 — PARTIALLY ACCURATE

- Documentation: README.md; docs/operations/docker.md.
- Section / approximate location: Quick start / Operating.
- Claim: Compose runs the complete application and queued work is described without a dispatcher start.
- Implementation: `docker-compose.yml; docker-compose.dev-stack.yml; ops/docker/entrypoint.sh`.
- Relevant tests: processing/tests.py and pyramid/measurement tests validate dispatch functions; no clean-container execution in this audit.
- Recommended change: Compose includes web app and PostgreSQL, not dispatcher service. Give explicit management-command dispatcher invocation and recommend operator supervision. Correct the obsolete claim that root manage.py fails in Docker; it adds backend to sys.path and Docker sets PYTHONPATH.
- Confidence: High.

### A16 — AMBIGUOUS

- Documentation: docs/engineering/measurements.md; docs/operations/docker.md.
- Section / approximate location: Deployment / Build profiles.
- Claim: Installing the deployed Docker profile suffices for measurements.
- Implementation: `Dockerfile installs ops/docker/requirements-*.txt; core manifest lacks kimimaro; AI manifests include core; requirements/release.in pins kimimaro`.
- Relevant tests: annotation/test_measurements.py requires kimimaro; no Docker measurement smoke found.
- Recommended change: Document missing Docker measurement dependency and separate runtime/dependency gap; use release/conda environment with measurement dependencies, do not claim stock Docker measurements work.
- Confidence: High (manifest); clean image not built.

### A17 — INCORRECT

- Documentation: docs/engineering/data-and-storage.md.
- Section / approximate location: Axis conventions / Registration formats.
- Claim: All formats reject non-singleton channel/time axes; HDF5 requires numeric 3-D.
- Implementation: `backend/annotation/visualization/slice_io.py: _open_volume; hdf5_io.py; nifti_io.py; backend/core/utils.py`.
- Relevant tests: volumes/test_metadata_parity.py; test_nifti.py; annotation/test_hdf5_source.py; test_hdf5_tiff_parity.py.
- Recommended change: Separate NIfTI trailing singleton validation and HDF5 leading singleton/2-D handling from TIFF reader flattening extra leading axes; do not claim TIFF C/T semantic validation.
- Confidence: High.

### A19 — PARTIALLY ACCURATE

- Documentation: docs/engineering/measurements.md.
- Section / approximate location: CSV/provenance / offline command.
- Claim: CSV preserves run/source/spacing/full precision (ambiguous for offline CSV).
- Implementation: `frontend/src/api/measurements.ts: measurementCSV; backend/annotation/management/commands/measure_mito.py; measurement_jobs.py`.
- Relevant tests: frontend/src/components/MitoMeasurements.test.tsx; backend/annotation/test_measure_mito_command.py; test_measurement_api.py.
- Recommended change: List exact 12-column browser CSV and four-column offline CSV with six significant digits; DB stores input fingerprint, JSON stores method/scope, neither export bundles full software/model provenance.
- Confidence: High.

### A20 — INCORRECT

- Documentation: docs/operations/docker.md.
- Section / approximate location: Upgrade profiles.
- Claim: legacy + build:no-demo pairs as default legacy without extra browser flags.
- Implementation: `frontend/package.json: build:no-demo; backend/core/checks.py; config/env/docker.env.example`.
- Relevant tests: backend/core/test_security_profile.py; frontend/src/pages/LoginPage.test.tsx.
- Recommended change: build:no-demo uses integrated chunk flags; keep legacy backend identity but enable matching nine backend features and VITE declarations for portable no-demo deployment. Do not change flags in code/config.
- Confidence: High.

### A21 — PARTIALLY ACCURATE

- Documentation: docs/research/methods.md; docs/research/claims-matrix.md.
- Section / approximate location: Interface organisation / derived history.
- Claim: No row per user action exists, and one reviewing manager owns each task.
- Implementation: `frontend/src/features/worklist/timeline.ts; backend/accounts/models.py: AuditEvent; annotation/models.py: ReviewRecord`.
- Relevant tests: backend/accounts/test_teams.py: AuditTrail tests; frontend/src/features/worklist/timeline.test.ts.
- Recommended change: Qualify derived conversation vs separate persisted audit events; reviews record deciding manager per round, not exclusive task-reviewer ownership.
- Confidence: High.

## P3 findings

### A22 — PARTIALLY ACCURATE

- Documentation: docs/user-guide/04-viewer.md; docs/user-guide/07-submit-and-review.md.
- Section / approximate location: Viewer context / review decisions.
- Claim: Official approved checkpoint and current label used as universal label terminology.
- Implementation: `backend/annotation/services.py: _visible_label_path, _install_submission_as_official; frontend/src/pages/ViewerPage.tsx`.
- Relevant tests: backend/annotation/test_source_volume_readonly.py; test_dual_submission_channels.py; frontend viewer focus/read-only tests.
- Recommended change: Prefer official label and saved working label; clarify task View and submission View source and zero-based URL/API coordinates vs one-based layer controls.
- Confidence: High.

### A23 — STALE

- Documentation: docs/user-guide/04-viewer.md; docs/user-guide/06-assisted-and-track.md.
- Section / approximate location: Jump to region / Region control.
- Claim: Only inside region mask is the control name; jump merely goes to an occupied plane.
- Implementation: `frontend/src/features/viewer/RegionOnlyButton.tsx; JumpToRegionButton.tsx; regionIndex.ts`.
- Relevant tests: AnnotationCanvasRegion.test.tsx; JumpToRegionButton.test.tsx; RegionOnlyButton.test.tsx.
- Recommended change: Use Region only; nearest occupied plane of current axis, disabled if already there or empty; overlay independent of segmentation.
- Confidence: High.

### A24 — PARTIALLY ACCURATE

- Documentation: docs/engineering/ai-and-algorithms.md.
- Section / approximate location: Track branch inference.
- Claim: All seeds smaller than configured minimum are discarded.
- Implementation: `backend/annotation/tracking/components.py`.
- Relevant tests: backend/annotation/test_tracking_branches.py: test_a_deliberately_tiny_prompt_is_never_silently_dropped.
- Recommended change: Mention speck filtering retains deliberate tiny prompt when no qualifying component survives.
- Confidence: High.

## Verified coverage and limits

| Required area | Implementation and test evidence | Finding |
| --- | --- | --- |
| Processing | `backend/processing/`, `core/choices.py`, processing tests, pyramid jobs, measurement API | Durable DB metadata; local and SLURM only; A13–A16 |
| SAM2 | `tracking/adapters/sam2.py`, `sam2_bridge.py`, `cellable_port/ai/`, mask and tracking tests | Shared SAM2.1 Hiera Large/config; interactive requests, not ProcessingJob; CUDA requirement A05 |
| Annotation state | `annotation/services.py`, `label_paths.py`, serialized-write, source-read-only, submit/review/dual-channel tests | Sources preserved; immutable snapshot bytes; working mutable; approval copies winner and re-seeds; A01/A02/A07 |
| ROI | `region_mask.py`, `volumes/region_masks.py`, Region canvas/outside-edit tests | Whole-volume instance membership; pixel ROI write guard for ordinary ROI Save; A04/A23 |
| Viewer | `ViewerPage.tsx`, `SliceViewer.tsx`, `coordinates.ts`, select-jump/axis-fit/region/read-only tests | Orthogonal axes, layer controls, pan/zoom/contrast/opacity, hide/solo/pin, Three.js meshes and focused links implemented; A22 |
| Tools | `AnnotationCanvas.tsx`, local flood/interpolation, plan services, `cellable_port`, whole-volume operation tests | Select is view state; brush/erase/box are plane edits; merge/delete volume ID operations; split/watershed bounded 3-D; A03/A08/A10 |
| Track | `tracking/services.py`, branching/contact/crop adapters, Track seeds/review/history/range tests | Inclusive axial range, bidirectional temporary branches, parent restoration, request-order classes and bounded crops; A09/A24 and B01 |
| Collaboration | `accounts/roles.py`, `accounts/teams.py`, annotation permission predicates, collaboration/member/assignment/review/comment tests | Managers edit unlocked tasks globally; assignee edits own unlocked task; memberships/team grants widen view without assigning; A02/A07 |
| Sharing | `projects/share_api.py`, annotation public token endpoints, `PublicSharePage`/`TaskSharePage`/`HardCaseSharePage`, public shares and API flow tests | Anonymous token reads; mutations require authenticated authorization; backend validates token scope against requested volume; A06 |
| Measurements | `measurements.py`, `measurement_jobs.py`, `measurement_spacing.py`, API/command and synthetic tests | Whole volume, official/saved working only; no pending edits/snapshot selection; nm engine/µm storage, 100-voxel skeleton dust; A16/A19 |
| Formats | `slice_io.py`, `hdf5_io.py`, `nifti_io.py`, `core/utils.py`, metadata/parity/NIfTI/spacing tests | ZYX array contract; unit normalization; no legacy reversed-axis view; A17 |
| Storage | `projects/services.py`, `core/data_root.py`, label paths, cleanup/source/ownership tests | Cleanup after commit, protects external/source/surviving paths and symlinks, failed removals logged; A18 |
| Deployment | Compose files, Dockerfile, entrypoint, hardware probe, env examples, security-profile tests | App/PostgreSQL; dev.yml DB only; dev-stack compiled SPA; no dispatcher service, no image kimimaro; A15/A16/A20 |
| Authentication/reset | `accounts/api.py`, `core/reset_api.py`, application reset services, mock-login/reset tests and `LoginPage.test.tsx` | Click-to-fill and explicit Sign in; backend allowlist plus build gate; dev reset separately gated with CSRF; production reset password/token/backup/freeze/identity protections preserved |
| Licensing | `LICENSE`, `THIRD_PARTY_NOTICES.md`, `docs/attribution.md`, SAM2/em_erl licenses, requirements | VERIFIED: no first-party license grant; SAM2 Apache-2.0, em_erl MIT, kimimaro GPL and GPL/LGPL dependency review unresolved; no licensing edits required |

## Ambiguities and suspected implementation bugs (no runtime changes)

- **B01 — Track persistence barrier:** propagate installs preview planes in the ordinary pending buffer before Confirm. `flushSafeDraft` checks ROI loss but not `trackingPendingReview`. A 30-second timer or hidden-tab flush can therefore attempt to save an unresolved preview. Track review tests cover immediate Confirm/Reject, not this delayed interaction. Review the intended barrier and add an integration regression before changing code. Until then documentation must not promise unconfirmed Track cannot reach disk; Reject restores pending geometry and requires a successful Save to restore disk after any earlier save.
- **B02 — deployment gaps:** Docker AI-CPU prose promised unsupported SAM2 CPU operation; image manifests omit kimimaro, and Compose starts no dispatcher. Treat these as deployment decisions rather than silently adding services/dependencies or enabling fallback in this documentation task.
- **B03 — withdrawal semantics:** official label can be repointed directly to the mutable working TIFF during team withdrawal. This behavior has explicit tests, so it must be documented. Maintainers should decide whether official should imply review approval and whether a separate immutable promotion copy is wanted.
- **B04 — durability limits:** local adapter cancellation records cancelled but does not terminate an already running subprocess; dispatcher lacks a lease/recovery path for interrupted native work. SLURM polling/cancellation are code-inspected, not proven against a live cluster. No automatic scientific output installation occurs in `on_job_finished`.
- **B05 — scientific provenance:** measurements retain filesystem identity/size/times and spacing in job config, method/scope in JSON; they do not hash label bytes or embed software version, kimimaro version, complete TEASAR parameters or approval evidence in CSV. Do not equate unchanged timestamps with cryptographic input identity. Offline command lacks web fingerprint checks and crop cap.
- **B07 — legacy task tokens:** stateless signed task tokens have neither per-link revocation nor age expiry. Maintainers must decide their compatibility/security policy; this audit documents the behavior without changing signing or routes.
- **B06 — security policy evidence:** `SECURITY.md` describes maintainer support/reporting policy; repository tests cannot establish that a private reporting channel or active release support is operational. Retained as policy, not certified runtime behavior.

- **B08 — historical source protection:** `_install_submission_as_official` overwrites the registered label reference and clears `label_file`; `_registered_paths` protects current references only. Dataset/project `_purge_tree` can remove an earlier imported label under the owned folder if no surviving volume references it. Existing source-protection tests do not cover this approval-then-delete sequence. Keep original inputs in a separate archive; maintainers should decide whether historical imports need durable references or separate protection. This is a suspected data-integrity gap, not an executed loss reproduction.

## Term and link audit

`local worker`, `GPU worker`, `GPU-assisted inference backend`, `region crop`, `automatic save`, `approved working label`, `current label`, `SLURM worker` had no exact matches in the maintained/legacy prose at baseline. `official checkpoint` occurs in development/review docs and is normalized where it obscures source semantics; official checkpoint file comments and checkpoint weights remain valid concepts. `documentation/` appears only in migration/layout explanations and the code-map compatibility pointer, not as a primary maintained source. Legacy pages forward to `docs/`. Broader CPU/save/approval searches found the issues above despite absence of those exact stale strings.

## Validation and remediation record

Pass one: report created; no other documentation or runtime file edited. Pass two: targeted corrections and cross-links are recorded after validation. The final token-path verification added A25 before qualifying revocation prose; the first report had 24 groups (4 P0), the completed audit has 25. Final cleanup inspection elevated A18 from P2 to P0 for the untested historical-source protection gap, yielding 6 P0, 8 P1, 8 P2 and 3 P3. Existing tests are evidence of specified behavior; execution results are reported separately and must not be inferred from having read a test.


### Completed corrections

The second pass updated save/preview persistence, ROI/protected edits, official
source transitions and reset, sharing permissions/revocation, dual submission
channels, prompt queue semantics, browser tools, SAM2/CUDA architecture, local
and SLURM processing behavior, file axes, measurements/provenance, retained
artifacts and deployment prerequisites. Navigation and viewer details were
aligned to frontend routes/tests. Dated host records retain their original
scope; licensing and product invariants remain unchanged. No runtime, test,
configuration or licensing file changed.

### Executed checks

| Check | Command / scope | Result |
| --- | --- | --- |
| Main baseline | `git fetch origin main`; compare HEAD/main/origin/main | All three `1c47b3bce8e032957687aba9ec658e026eae1ac7` |
| Internal Markdown links | `python scripts/docs/check_links.py` | Passed; 306 local inline links across 68 Markdown files, 0 errors |
| Whitespace | `git diff --check` | Passed |
| Referenced implementation files | Check concrete backend/frontend/ops/scripts/config/vendor/requirements file paths in README/docs | Passed; 54 unique concrete file references, 0 errors |
| Frontend suite | `npm test --prefix frontend` | 89 files / 678 tests passed, 9.71 s |
| TypeScript | `npm run typecheck --prefix frontend` | Passed |
| Production frontend build | `npm run build:production --prefix frontend` | Passed; Vite build 3.32 s |
| Browser scenarios | In frontend: `npx playwright test --config playwright.scientific.config.ts` | 9 passed, 20.3 s; synthetic API mocks |
| Backend tests and Django system checks | Selected modules below, `manage.py test ... --noinput` | 744 tests, 540.142 s; OK, skipped=6; system check no issues |
| Stale terminology/legacy routing | Requested term searches across README/docs/documentation, plus broader save/CPU/approval searches | Remaining occurrences are qualified audit claims, current-reference prose or compatibility pointers |

No repository Markdown formatter/linter is configured; link validation and
`git diff --check` were the available documentation checks. External URL
availability was not tested. Local link targets and heading anchors were checked;
concrete implementation-file references were also checked, with `config/urls.py`
resolved as the documented backend-relative shorthand.

Backend execution used `/tmp/mito-doc-audit-venv/bin/python`, a temporary
`--system-site-packages` virtualenv over the development Conda Python. Kimimaro
5.8.5 was installed there for real measurement tests; neither repository
requirements nor the base environment were changed. Environment:
`MITO_DB_ENGINE=sqlite`, isolated `/tmp/mito-doc-audit.sqlite`,
`MITO_DATA_ROOT=/tmp/mito-doc-audit-data`,
`MITO_SHARED_STORAGE_ROOT=/tmp/mito-doc-audit-shared`, `DJANGO_DEBUG=true`,
`MITO_UPGRADE_PROFILE=legacy`, and all nine `FEATURE_*` settings set to `1`.
Test fixtures further isolate volumetric writes. From `backend/`, the exact
module selection was:

```sh
/tmp/mito-doc-audit-venv/bin/python manage.py test \
  processing accounts.test_mock_login accounts.test_collaboration_api \
  accounts.test_admin_collaboration accounts.test_teams projects.tests \
  projects.test_members_api projects.test_public_shares \
  annotation.test_whole_volume_ops_api annotation.test_serialized_writes \
  annotation.test_submit_loop annotation.test_review_loop \
  annotation.test_dual_submission_channels annotation.test_review_label_comments \
  annotation.test_region_mask_roi annotation.test_region_display \
  annotation.test_tracking annotation.test_tracking_branches annotation.test_tracking_crop \
  annotation.cellable_port.ai.test_mask_backend \
  annotation.cellable_port.ai.test_sam2_feature_cache annotation.cellable_port.ai.test_prompt_roi \
  annotation.test_measurements annotation.test_measurement_api \
  annotation.test_measurement_spacing annotation.test_measure_mito_command \
  annotation.test_tools annotation.test_flood_fill_api annotation.test_interpolation_api \
  annotation.test_hdf5_source annotation.test_hdf5_tiff_parity \
  annotation.test_reset_working_labels annotation.test_source_volume_readonly \
  annotation.test_hard_case_category annotation.test_time_tracking \
  volumes.test_nifti volumes.test_metadata_parity volumes.test_pyramid_jobs \
  volumes.test_region_mask_coverage core.test_reset_api core.test_application_reset \
  core.test_security_profile core.test_storage_permissions --noinput
```

Six skipped tests are the PostgreSQL-only ConcurrentSubmitTests (2),
ConcurrentApproveTests (1) and ConcurrentToolTests (3). This was a substantial
targeted backend run, not the entire backend suite or release-lock certification.
Logs are local `/tmp/mito-doc-audit-{backend,frontend,typecheck,build,browser}.log`;
they are not committed artifacts. Runtime ERROR/WARNING messages from negative
API/failure fixtures did not produce test failures.

### Remaining verification and documentation debt

- Resolve B01–B08 before strengthening persistence, cleanup, scientific provenance
  or share-lifetime promises; add regression coverage in a separate code task.
- Validate live CUDA/checkpoint inference, SLURM submission/poll/cancel and shared
  storage; this audit checked code and mocked tests only.
- Run PostgreSQL concurrency tests and the full backend suite in the release lock.
- Validate Docker profiles and measurement runtime/dispatcher integration on a
  clean host; no deployment or runtime manifest was changed here.
- Exercise representative large biological volumes and validate methods for each
  study; synthetic correctness checks do not establish biological accuracy.
- Resolve first-party license grants and third-party/vendored provenance through
  maintainer/legal decisions; this audit did not certify redistribution rights.
- Existing historical validation/host pages remain dated evidence. External links
  and actual private security-reporting operations remain unverified.

### Documentation files changed

- README.md
- docs/development.md
- docs/engineering/ai-and-algorithms.md
- docs/engineering/architecture.md
- docs/engineering/data-and-storage.md
- docs/engineering/measurements.md
- docs/getting-started/README.md
- docs/getting-started/glossary.md
- docs/index.md
- docs/operations/docker.md
- docs/operations/hardware-adaptive.md
- docs/operations/production-host.md
- docs/operations/reference-hardware.md
- docs/overview.md
- docs/release/checklist.md
- docs/release/validation-history.md
- docs/research/README.md
- docs/research/claims-matrix.md
- docs/research/documentation-audit.md
- docs/research/methods.md
- docs/user-guide.md
- docs/user-guide/01-roles-and-navigation.md
- docs/user-guide/02-projects-and-data.md
- docs/user-guide/03-people-and-assignment.md
- docs/user-guide/04-viewer.md
- docs/user-guide/05-annotation-tools.md
- docs/user-guide/06-assisted-and-track.md
- docs/user-guide/07-submit-and-review.md
- docs/user-guide/08-collaboration-and-safety.md
- docs/user-guide/09-measurements.md
- docs/user-guide/workflows.md


## English documentation follow-up — 2026-10-09

After audit commit `46832d8`, translated the remaining Chinese onboarding,
indexes, measurements guide, engineering reading guides, directory READMEs and
compatibility forwarding pages into English. Removed the stale “Chinese
onboarding guide” link label. `docs/` remains the maintained documentation tree.
Kept UI labels, routes, filenames, permissions, units and pending/saved/snapshot
terminology aligned with the implementation. Explicitly distinguished browser-only
pending edits from edits already persisted by autosave in the Measurements
walkthrough. No runtime, test, configuration or licensing changes were made.
The preceding severity counts and file list describe the original audit pass.

Follow-up checks:

- Scanned all 68 tracked Markdown files: no Chinese characters remain.
- `python scripts/docs/check_links.py`: 311 local links, zero errors.
- Checked 54 unique concrete implementation-file references: zero missing files.
- `git diff --check`: passed.
- `npm test --prefix frontend -- src/components/ProjectMeasurements.test.tsx src/components/MitoMeasurements.test.tsx`: 14 tests passed.
- In an isolated SQLite/test-storage environment, reran `annotation.test_measurement_api` and `annotation.test_measurement_spacing`: 19 tests, 18 passed and one error, twice. The failure was `MeasurementSpacingTests.test_nifti_requires_declared_spatial_units`; it passed in the earlier 744-test run. Do not interpret that earlier run as proof this case is stable.

**B09 — header-cache invalidation:** `backend/core/utils.py:_header_cached`
keys header reads by kind, path and floating-point modification time. The
NIfTI spacing test rewrites an unknown-unit file with declared micron units at
the same path. A follow-up probe reproduced unchanged modification timestamps
in 22 of 30 rapid writes, yielding stale cached unknown spacing each time despite
valid updated units. Direct `nifti_voxel_size_zyx` reads or `clear_header_cache`
recovered the calibrated values in every stale case. This is an implementation
limitation exposed by the existing test, not a translation regression. Registered
sources should remain stable; maintainers should decide how to invalidate header
metadata reliably after replacement and make this regression deterministic in a
separate code task. Runtime behavior was not changed in this documentation pass.
