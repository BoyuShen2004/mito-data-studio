# Project extension contract and contribution guidelines

Research tools use one project **Extensions** tab, always last in each role's
tab list. Measurements is the first registered example. Annotation, assignment
and review remain core sections. This is a frontend composition contract, not
a runtime plugin installer or automatic backend plugin framework.

## Implementation boundaries

| File | Responsibility |
| --- | --- |
| [types.ts](../../frontend/src/features/extensions/types.ts) | Typed definition and project context |
| [registry.ts](../../frontend/src/features/extensions/registry.ts) | Trusted repository-owned entries, visibility, enabled state, URLs and legacy aliases |
| [ProjectExtensions.tsx](../../frontend/src/features/extensions/ProjectExtensions.tsx) | Catalog, selected workspace, lazy loading and render/load error boundary |
| [ProjectDetailPage.tsx](../../frontend/src/pages/ProjectDetailPage.tsx) | Core tab order, project access, shared volume list and legacy URL normalization |
| [Measurements adapter](../../frontend/src/features/extensions/measurements/extension.tsx) | Connects context to existing measurement components |

`ProjectExtensionContext` supplies `project`, `isManager` and the shared
`volumes` async state, including loading, error and reload. Reuse that list
instead of fetching it again. Each tool owns its form, requests and result
state. Context indicates UI capabilities; the backend must authorize requests
independently. The host does not save labels or start jobs.

The canonical URL is
`/projects/:id?tab=extensions&extension=measurements&volume=:id`.
Use `projectExtensionHref` for links and `projectExtensionSearch` for selection.
IDs are stable, unique lowercase names with optional hyphens. Preserve published
IDs. Existing `tab=measurements` links are normalized with history replacement,
retaining other parameters, including volume. Returning to the catalog clears
workspace parameters.

Only the selected enabled component is mounted. Render errors and lazy-import
failures are caught below core tabs. This does not isolate trusted JavaScript,
catch arbitrary asynchronous errors or recover backend jobs. Reload to retry a
failed import because React caches its rejection. Tools handle their own request
and job failures.

## Add a research tool

1. Create `frontend/src/features/extensions/<id>/extension.tsx` with a default
   component accepting `ProjectExtensionContext`. Keep domain logic in its own
   modules and compose reusable components where appropriate.
2. Add one entry in `registry.ts` with `id`, `title`, `description`,
   `accessDescription`, `enabled` and a lazy `component`. Optional
   `isVisible(context)` hides a whole tool; retain access for permitted read-only
   viewers. New tools need no legacy tab alias or changes to core tabs.
3. Add scoped backend APIs and server-side authorization if computation or stored
   data is required. Frontend registration does not supply those services.
4. Add behavioral tests and English user instructions, linked from the
   documentation index and user guide.

A registry entry can use this template:

```tsx
{
  id: "research-tool",
  title: "Research tool",
  description: "Describe the implemented scientific operation.",
  accessDescription: () => "Describe the tool's actual role permissions.",
  enabled: true,
  component: lazy(() => import("./research-tool/extension")),
}
```

This is a contribution template, not another installed tool. Measurements
demonstrates request-to-job integration in the
[feature walkthrough](feature-walkthrough.md). Its science still resides in
`backend/annotation/measurement_*.py` and `measurements.py`; the catalog change
does not alter computation, provenance, limits or saved-label sources.

## Backend and data requirements

- Check authentication, project/volume access and action permissions server-side.
  Hidden or disabled frontend entries are not authorization boundaries.
- Define accepted sources, formats, axis order, calibration, output units and
  scientific limitations. Preserve registered sources, working labels and
  immutable snapshots. Analysis must not implicitly save, submit or approve.
- Keep migrations additive. Define generated-file ownership, cleanup, history
  retention and stale-result detection. Avoid overwriting another tool's files
  or making persisted results depend on browser-only state.
- Use existing durable processing infrastructure where appropriate. New job types
  need explicit backend routing and dispatcher support; the frontend registry
  does not register runners. See [background processing](architecture.md#background-processing)
  and [measurement prerequisites](measurements.md#deployment-prerequisites).
- Bound server memory and crops for large arrays. Poll active jobs only, clean up
  timers and ignore stale responses on unmount or input changes. Lazy loading
  alone is not evidence of scientific or deployment scalability.

## Disable or remove a tool

Set the registry entry's `enabled` to `false`, then rebuild and deploy the
frontend. The catalog omits it, its direct URL shows unavailable, and optional
shortcuts using `isProjectExtensionEnabled` disappear. Backend APIs, queued jobs
and stored results remain. Catalog membership is separate from the nine aligned
application `FEATURE_*` / `VITE_*` flags; do not repurpose them.

Removing a definition removes its aliases too. Keep a disabled entry to let old
links resolve to an unavailable workspace. Before removing backend services or
dependencies, address active jobs, result retention, API clients and cleanup.
UI removal alone is not backend decommissioning.

## Validation checklist

- Verify two tools can be listed and selected without changing the host; unopened
  and disabled components must not load.
- Test unknown/disabled URLs, failure recovery and rightmost tab order for manager
  and annotator views. Catalog/core navigation must remain usable.
- Test server authorization separately: read-only access, permitted mutations,
  scope tampering and scientific input validation.
- Cover explicit runs, failures, changed inputs, stale results and preservation
  of pending input during unrelated refreshes. Catalog entry must issue no writes
  or annotation changes.
- Maintain English workflow docs, API expectations and method limits. Run
  typecheck, relevant unit/browser/backend tests and documentation link checks.

See [registry tests](../../frontend/src/features/extensions/registry.test.ts),
[host tests](../../frontend/src/features/extensions/ProjectExtensions.test.tsx)
and [browser workflows](../../frontend/e2e/scientific-workbench.spec.ts).

## Research description and licensing

Measurements is an implemented example of adding a research tool through the
shared contract. Arbitrary third-party plugin compatibility, installation
without rebuilding, biological validity or measured scalability require further
evidence. The current [LICENSE](../../LICENSE) grants no first-party reuse
license; extensibility does not change this status or justify calling the
software open source. See the [claims matrix](../research/claims-matrix.md) and
[third-party notices](../../THIRD_PARTY_NOTICES.md).
