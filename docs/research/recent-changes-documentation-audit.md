# Recent-changes documentation audit — 2026-10-10 UTC

## Baseline, scope and method

Audit baseline: local main `12ad4bc` plus the authorized, uncommitted
assistant-manager and People refinements already deployed to development.
This report was completed before this pass changed maintained guides. It covers
recent code/history and documentation discoverability, not a new exhaustive audit
of every scientific algorithm. The [earlier repository audit](documentation-audit.md)
retains its findings and unresolved software behavior.

Reviewed recent commits: `6def3ee` (registration/team navigation), `ed62d3f`
(draft ownership/polling), `279b09e` (Track layout), `e8789f4` (Extensions),
`12ad4bc` (selective disclosure), and current account/People changes.
Implementation and tests take priority over prose. Paths below are relative to
repository root. Line numbers identify the pre-update sections approximately.

Issue counts: **P0: 0; P1: 4; P2: 7; P3: 4**. These are documentation findings;
they are not a certification that the software has no security/data risks.

## P0 — no new documentation finding in the reviewed changes

Existing warnings about unresolved Track-preview autosave, withdrawal promotion,
legacy task-share revocation, header-cache invalidation and source archival remain
valid audit debt. Licensing still grants no first-party reuse permission.

## P1 — user workflow

| ID | Documentation / section | Claim or omission | Implementation source | Relevant tests | Classification | Recommended change | Confidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A01 | README.md, Roles at a glance (~83) | Lists three roles without the added dual-role capability or its grant boundary | backend/accounts/roles.py; accounts/api.py AssistantManagerView; frontend/src/components/Navbar.tsx | backend/accounts/test_assistant_managers.py; frontend/e2e/scientific-workbench.spec.ts | UNDOCUMENTED IMPLEMENTED BEHAVIOR | Explain primary managers, assistant grants, both workspaces and profile entry; link guide | High |
| A02 | docs/getting-started/README.md, First day (~15) | Onboarding lacks the grant/refresh/switch route and Extensions discovery | Navbar.tsx; pages/PeoplePage.tsx; features/extensions/registry.ts | scientific-workbench.spec.ts assistant switch, grant/revoke and Extensions scenarios | UNDOCUMENTED IMPLEMENTED BEHAVIOR | Add a dual-role route and explicit Extensions → Measurements entry | High |
| A03 | docs/user-guide/08-collaboration-and-safety.md, Annotation time/Profile (~69–90) | Username directly edits profile; roster Time wording lacks role/group limits | Navbar.tsx; pages/PeoplePage.tsx; pages/PersonPage.tsx | PeoplePage.test.tsx; scientific-workbench.spec.ts | STALE | Describe dropdown → Your profile; ordinary annotator and assistant roster omit Time; account-access controls come last | High |
| A12 | docs/user-guide/workflows.md, Assign work (~70) | Goes from staged assignee/metadata to annotator work without Save plan; also omits registration continuation | pages/ProjectDetailPage.tsx; pages/RegisterDataPage.tsx; assignment components | ProjectDetailPage.test.tsx; RegisterDataPage.test.tsx; scientific-workbench.spec.ts | PARTIALLY ACCURATE | Include Save plan; explain inline team changes retain pending assignment input and registration returns to Data | High |

## P2 — architecture, contribution and operations

| ID | Documentation / section | Claim or omission | Implementation source | Relevant tests | Classification | Recommended change | Confidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A04 | docs/getting-started/glossary.md, term table (~3) | Missing Extension, primary/assistant manager and active workspace definitions | accounts/roles.py; features/extensions/types.ts, registry.ts | test_assistant_managers.py; registry.test.ts | UNDOCUMENTED IMPLEMENTED BEHAVIOR | Add distinct capability/workspace and core/extension terminology | High |
| A05 | docs/overview.md, Roles/Measurements (~17/65) | Three-role framing and a Measurements-only feature heading understate the implemented composition design | roles.py; ProjectDetailPage.tsx; features/extensions/ | test_assistant_managers.py; ProjectExtensions.test.tsx | PARTIALLY ACCURATE | Explain dual capability and core workflow + research-tool catalog; only Measurements currently registered | High |
| A06 | CONTRIBUTING.md, Maintainable workflow changes (~27); docs/engineering/code-map.md, Find a feature (~68) | New auth predicates, grant endpoint and role-sensitive contribution rules absent | accounts/authentication.py, roles.py, api.py; api/client.ts | test_assistant_managers.py; api/workspace.test.ts | UNDOCUMENTED IMPLEMENTED BEHAVIOR | Link dedicated role design and require effective-role authorization/durable-role identity checks | High |
| A07 | docs/engineering/architecture.md, Dual-role workspaces (end) | Brief implementation summary exists but no focused API/state/error contract | authentication.py; api.py WorkspaceRoleView/AssistantManagerView; api/auth.ts; accounts/0014 | test_assistant_managers.py; workspace.test.ts | PARTIALLY ACCURATE | Add a focused engineering reference including defaults, revocation, status codes, audit, migration and session-admin boundary | High |
| A08 | docs/operations/production-host.md, Code updates (~63) | Generic migrations documented; assistant grant rollout and production role checks absent | accounts/0014; settings.py; authentication.py; Navbar.tsx | test_assistant_managers.py; production login browser check | UNDOCUMENTED IMPLEMENTED BEHAVIOR | Explain additive migration, default-false grants, preserve production env, role smoke checks and rollback limits | High |
| A09 | docs/release/checklist.md, implementation gaps (~42); docs/research/reproducibility.md, experiment archive (~44) | No extension-registry/dual-role release or experiment context | registry.ts; roles.py; accounts/0014 | registry.test.ts; test_assistant_managers.py | UNDOCUMENTED IMPLEMENTED BEHAVIOR | Record enabled tools, actual commit/build, active workspace and grant boundaries; preserve dated captures | High |
| A10 | docs/research/methods.md, Quality/Measurements (~117/136); docs/research/claims-matrix.md, table (~7) | Extensions evidence exists in claims but Methods lacks its design rationale; dual-role implementation absent | features/extensions/; accounts/authentication.py, roles.py | ProjectExtensions.test.tsx; registry.test.ts; test_assistant_managers.py | UNDOCUMENTED IMPLEMENTED BEHAVIOR | Describe implemented composition and checked capability; avoid runtime plugin, licensing, scalability or productivity claims | High |

## P3 — terminology, navigation and evidence

| ID | Documentation / section | Claim or omission | Implementation source | Relevant tests | Classification | Recommended change | Confidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A11 | docs/user-guide/09-measurements.md, Roles (~9); docs/user-guide/extensions.md, Access (~7); docs/engineering/feature-walkthrough.md, API (~34) | Manager means active workspace, but assistant permission changes are implicit | roles.py; measurement_api.py; AuthContext.tsx; registry.ts | test_assistant_managers.py; test_measurement_api.py; scientific-workbench.spec.ts | PARTIALLY ACCURATE | Clarify assistant in Manager mode can run; Annotator mode retains read/export access | High |
| A13 | docs/research/progressive-disclosure-audit.md, final decision table (~19) | Older singleton/Time policy predates assistant roster exception | PeoplePage.tsx; styles.css .people-access-actions | PeoplePage.test.tsx; scientific-workbench.spec.ts narrow-screen gap assertion | STALE | Preserve historical scope and add current assistant exception, single role summary and separated final action area | High |
| A14 | docs/index.md, entry table (~10); docs/user-guide.md, chapter table (~15); docs/research/README.md (~6) | New role/design/audit material has no concise discovery route | Navbar.tsx; accounts/roles.py; features/extensions/ | Corresponding auth/extension tests | UNDOCUMENTED IMPLEMENTED BEHAVIOR | Link new engineering role reference, design principles and this audit | High |
| A15 | docs/release/validation-history.md, latest dated record (~6) | No dated evidence for the recent combined candidate; old records are valid history | Current tests/build and host-local dev verification manifests | Current frontend/backend/browser suites | UNDOCUMENTED IMPLEMENTED BEHAVIOR | Append actual checks and limits; do not replace historical counts or claim full release certification | High |

## Verified recent behavior

- Extensions is rightmost for all roles; Measurements is the only built-in entry.
  Legacy Measurements links normalize to the catalog workspace. Registration is
  repository-owned, typed and lazy; disabling UI keeps backend APIs/jobs/results.
- Measurement selection survives spacing saves and unrelated refreshes; polling
  stops for terminal jobs. Run remains explicit and manager-authorized. Scientific
  logic is separate from frontend composition and generic processing infrastructure.
- Inline team changes preserve the assignment editor. Registration continues to
  the owning project's Data tab. Metadata drafts survive unrelated pyramid polls.
- Track rail/editor layout changes are present in main; existing scientific
  persistence warnings remain. This audit does not validate live SAM2 or GPUs.
- Profile, single-purpose reports and ordinary singleton lists remain visible;
  multi-record groups fold. Assistant managers is explicitly foldable even at one,
  has no Time report, shows one dual-role summary, and places grant/revoke last.
  Annotator Time and account-access controls are separated by spacing and a rule.
- Assistant grant defaults false, is recorded separately from annotator identity,
  is restricted to primary manager identities and is audited when changed.
  Request-selected roles are validated against grants; revocation rejects later
  manager requests. Switching reloads to Home after unsaved-input confirmation.

## Remaining boundaries and maintainer decisions

Primary manager means any existing base `manager` identity or superuser, not a
new singular lab-owner designation. Assistant mode has global application-manager
scope, not per-project delegation, and does not grant Django staff/superuser
status. These are current implementation facts; narrower delegation would be a
separate feature. No grant was automatically assigned during development rollout.

The shared extension contract is frontend composition. Backend authorization,
scientific provenance, cleanup and processing routing remain explicit work for
contributors. Runtime installation, automatic third-party plugin compatibility,
biological validity and measured scalability remain unimplemented/unproven.

Unresolved findings from the original audit are not fixed by these documentation
changes. First-party licensing still requires a maintainer decision. Preserve
historical deployment captures, and cite the actual commit instead of the reused
1.1.5 package string. This pass does not change runtime to make prose true.

## Second pass and validation

The following documentation pass addresses A01–A15 and commits previously
user-authorized feature changes with the documentation. Validation passed: 717 frontend tests across 95 files; 299 targeted backend tests
on isolated PostgreSQL storage; TypeScript and production frontend build; 409
local links/anchors across 75 Markdown files; English-language scan and git
whitespace check. No Markdown formatter/linter is configured. Production
deployment evidence is kept in a host-local backup/verification directory. Local commit and production
promotion are requested; pushing to the remote is explicitly excluded.

## Documentation files changed

- `CHANGELOG.md`
- `CONTRIBUTING.md`
- `README.md`
- `SECURITY.md`
- `docs/engineering/architecture.md`
- `docs/engineering/code-map.md`
- `docs/engineering/extensions.md`
- `docs/engineering/feature-walkthrough.md`
- `docs/engineering/roles-and-workspaces.md`
- `docs/getting-started/README.md`
- `docs/getting-started/glossary.md`
- `docs/index.md`
- `docs/operations/production-host.md`
- `docs/overview.md`
- `docs/product-invariants.md`
- `docs/release/checklist.md`
- `docs/release/validation-history.md`
- `docs/research/README.md`
- `docs/research/claims-matrix.md`
- `docs/research/methods.md`
- `docs/research/progressive-disclosure-audit.md`
- `docs/research/recent-changes-documentation-audit.md`
- `docs/research/reproducibility.md`
- `docs/user-guide.md`
- `docs/user-guide/01-roles-and-navigation.md`
- `docs/user-guide/03-people-and-assignment.md`
- `docs/user-guide/08-collaboration-and-safety.md`
- `docs/user-guide/09-measurements.md`
- `docs/user-guide/extensions.md`
- `docs/user-guide/progressive-disclosure.md`
- `docs/user-guide/workflows.md`
