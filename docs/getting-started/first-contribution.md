# First contribution: start with a small change

Read the [code map](../engineering/code-map.md) and
[product invariants](../product-invariants.md) first. A documentation correction
or a display fix on an existing page is a suitable first contribution. Canvas
rewrites, authorization changes and database migrations need broader context.

## Prepare an isolated development environment

1. Confirm you are in your own development checkout, rather than the lab's
   running development or production deployment directory.
2. Follow the [development guide](../development.md), using your own database
   and `MITO_DATA_ROOT`. Do not copy the production `.env`.
3. With a clean worktree, update main and create a branch, for example:

   ```bash
   git switch main
   git pull --ff-only
   git switch -c docs/explain-measurements
   ```

   If `git status` shows unfinished work, preserve or finish it before switching.
   Do not discard it with reset.

## Find the files, change a small scope, then verify

- Documentation: put maintained content in the appropriate `docs/` directory
  and link from the index. Keep compatibility pages as forwarding material.
- Frontend: follow the route to a page, then its components. Read adjacent tests
  and preserve button behavior and the meaning of application state.
- Backend: follow the URL to the API, service and tests. Frontend visibility
  does not substitute for backend authorization.

From the repository root in your configured development environment:

```bash
python scripts/docs/check_links.py
npm run typecheck --prefix frontend
npm test --prefix frontend
```

This backend example uses temporary SQLite and file storage. Activate the project
Python environment first:

```bash
mito_test_dir=$(mktemp -d)
(
  cd backend
  MITO_DB_ENGINE=sqlite MITO_SQLITE_NAME="$mito_test_dir/test.sqlite" \
  MITO_DATA_ROOT="$mito_test_dir/data" \
  python manage.py test annotation.test_measurements --noinput
)
```

This checks one module; it does not replace the backend suite or PostgreSQL
concurrency tests. Run the full suite from `backend/` with
`python manage.py test --noinput`, using your own test environment, and confirm
that a nonzero number of tests was discovered. Measurement tests require the
measurement dependencies. Choose checks appropriate to the change; translating
documentation does not require live GPU inference tests.

Check the production frontend with `npm run build:production --prefix frontend`.
`npm run build` / `make build` retains the development-account build flag;
use the explicit production build for deployment. Browser regressions use
`frontend/playwright.scientific.config.ts`, whose current browser path is
`/snap/bin/chromium`. Configure an available browser when using another host.

## Prepare for review

Run `git diff --check` and inspect `git diff` for unrelated edits, data and secrets.
Describe the original problem, resulting behavior, checks actually run and any
unverified scope in the PR. Documentation should be understandable without the
chat history. Never report tests as passed if they were not run.

See [CONTRIBUTING](../../CONTRIBUTING.md) for the full workflow. Merging a PR and
deploying a service are separate actions; merging alone does not establish that
production can be updated safely.
