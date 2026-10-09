# Frontend reading guide

React/TypeScript handles pages, the microscopy workbench and pending edits.
Backend APIs enforce authorization and persistence.

Follow [main.tsx](src/main.tsx) → [AppRoutes.tsx](src/routes/AppRoutes.tsx) →
`pages/` → `components/` or `features/` → `api/`. Adjacent `*.test.tsx` /
`*.test.ts` files show behavioral examples; `e2e/` contains browser regressions.

From this directory, use `npm run dev`, `npm run typecheck` and `npm test`.
Build production assets with `npm run build:production`; ordinary `npm run build`
includes the development-account build flag.

Continue with the [code map](../docs/engineering/code-map.md),
[feature walkthrough](../docs/engineering/feature-walkthrough.md) and
[first contribution](../docs/getting-started/first-contribution.md).
