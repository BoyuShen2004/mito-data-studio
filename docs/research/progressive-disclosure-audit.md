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

## Validation requirements

Verify section → entity expansion, independent siblings, keyboard operation,
draft retention through collapse and refresh, existing mutation payloads and
team deletion confirmation. Check dataset expansion, role restrictions,
read-only measurement entry, login/reset invariants and existing editor flows.
Run frontend typecheck/unit/browser tests and documentation link checks; deploy
to development only, without committing this change.
