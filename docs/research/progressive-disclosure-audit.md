# Progressive disclosure audit — 2026-10-09

## Scope and evidence

This frontend audit follows the request to show section summaries first and
expand individual teams or people for details. It inspects route pages and
shared components, with existing frontend tests as regression evidence. It is
an interface audit, not a backend security or scientific-method audit.

## Revised scope after interface review

The first pass folded too many single-purpose sections. User feedback narrowed
this to parallel groups with verbose repeated entries. The decisions below
supersede that broader first pass.

| Surface / source | Final decision |
| --- | --- |
| `pages/PeoplePage.tsx` | Keep profile and personal project lists visible. Collapse multi-person rosters and individual records; display singleton/empty rosters directly |
| `components/CollaborationManager.tsx` | Keep an existing-teams management group; fold individual teams only when there are multiple. New team is a plain form; with no teams it is directly visible |
| `components/DatasetsCard.tsx` | Fold individual datasets only for a multi-dataset project; show a lone dataset's metadata and volumes directly |
| `pages/ProjectDetailPage.tsx`, `PersonPage.tsx`, `RegisterDataPage.tsx` | Restore directly visible workload, access-member tables, personal project reports and registration metadata |
| `components/AnnotatorTimeSection.tsx`, `pages/TaskDetailPage.tsx` | Preserve existing report drill-down and review-round details |
| Home, project/task/case lists, viewer, volume detail, sharing | Keep primary lists, scientific context, tools and decision controls visible |
| Login, profile, administrator reset and Extensions/Measurements | Preserve existing workflows, warnings and permissions |

## Shared design

Use a reusable accessible disclosure only for suitable groups, with a plain
`collapsible=false` mode for singleton collections. Use a real button, `aria-expanded` and
`aria-controls`. Mount its body on the first expansion, then retain it hidden
when collapsed so pending forms survive. Expansion never saves data or grants
permissions. Use entity keys and preserve expanded state on unrelated refreshes;
leaving the page still unmounts its forms.

The overview and collaboration APIs still return their existing scoped payloads.
Collapsing improves presentation and delays mounting child UI; it does not
introduce server pagination or claim reduced API payload size. Large-list search
and pagination remain separate work.

## Assistant-manager follow-up — 2026-10-10 UTC

The later dual-role implementation explicitly makes Assistant managers foldable
at any count and omits its Time report. Ordinary singleton rosters retain direct
details. Each PersonCard has one role summary (including both roles), with
account grant/revoke controls in a final separated action area. Annotator Time
and account actions have a divider and spacing; the narrow-screen browser test
checks their separation. Ordinary annotators omit People/person Time controls.
See the [recent-changes audit](recent-changes-documentation-audit.md).

## Validation requirements

Verify section → entity expansion, independent siblings, keyboard operation,
draft retention through collapse and refresh, existing mutation payloads and
team deletion confirmation. Check dataset expansion, role restrictions,
read-only measurement entry, login/reset invariants and existing editor flows.
Run frontend typecheck/unit/browser tests and documentation link checks; deploy
to development first. The original pass requested no commit; subsequent user
authorization requested a local commit and production promotion. These deployment
instructions describe the audit history, not an automatic approval policy.
