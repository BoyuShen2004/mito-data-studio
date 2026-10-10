# Code map: trace a page to its implementation

Read [getting started](../getting-started/README.md) first. Choose one feature
and follow its page, API, backend service and tests rather than reading the
entire repository sequentially.

## Top-level directories

| Path | Contents | When to read it |
| --- | --- | --- |
| [frontend/](../../frontend/) | Browser UI, interactions and frontend tests | Changing pages, tools or display |
| [backend/](../../backend/) | Django APIs, authorization, models, algorithms and processing jobs | Understanding persistence, review and measurements |
| [docs/](../index.md) | Maintained documentation | Using, developing, operating or researching the application |
| [documentation/](../../documentation/README.md) | Compatibility forwarding pages | Following old links; add maintained content under docs |
| [scripts/](../../scripts/) | Development and documentation checks | Repeating local checks |
| [ops/](../../ops/) | Docker, staging, production and release tooling | Maintainer deployment work |
| [vendor/](../../vendor/) | Optional model resources and third-party assets | AI setup or attribution review |
| [manage.py](../../manage.py), [Makefile](../../Makefile) | Command entry points | After configuring your environment |
| [environment.yml](../../environment.yml), [requirements/release.txt](../../requirements/release.txt) | Development environment and hashed release dependency lock | Distinguishing development from a reproducible release environment |
| [frontend/package.json](../../frontend/package.json), [frontend/package-lock.json](../../frontend/package-lock.json) | Frontend scripts and dependency lock | Checking npm commands and exact dependencies |

Local `var/`, `data/`, `logs/`, `venv/`, `node_modules/` and `dist/` directories
usually hold runtime data, environments or build artifacts. They are not source
to reorganize casually. Real `.env` files are untracked; `.env.*.example` files
are templates.

## Understand the application layers

The frontend is React/TypeScript running in the browser; the backend is
Django/Python on the server. APIs connect them. Database records describe users,
tasks and file locations; microscopy and label arrays live in filesystem storage.
UI wording usually changes in the frontend. Permissions, persistence and
computation require understanding backend rules.

See [root files](root-files.md) for command/configuration entry points and moved paths.

## Read the frontend

| Order | File or directory | Purpose |
| --- | --- | --- |
| 1 | [main.tsx](../../frontend/src/main.tsx) → [AppRoutes.tsx](../../frontend/src/routes/AppRoutes.tsx) | Application startup and URL routing |
| 2 | [pages/](../../frontend/src/pages/) | Home, ProjectDetail, TaskDetail, Viewer and other page composition |
| 3 | [components/](../../frontend/src/components/) | Reusable lists, forms, review controls and measurement panels |
| 4 | [api/](../../frontend/src/api/), [types/](../../frontend/src/types/) | Backend requests and data types |
| 5 | [features/viewer/](../../frontend/src/features/viewer/) | Canvas, editing state and tools; follow a feature through the large canvas module |
| 6 | [features/extensions/](../../frontend/src/features/extensions/) | Project research-tool registry, typed context and lazy workspace; see [extension guidelines](extensions.md) |
| 7 | Adjacent `*.test.tsx` / `*.test.ts`, [e2e/](../../frontend/e2e/) | Behavioral examples and regression constraints |

## Read the backend

Start from [config/urls.py](../../backend/config/urls.py), then find the app's API,
service, models and tests. Module boundaries differ between apps; a feature need
not live entirely in one `services.py`.

| App | Responsibility |
| --- | --- |
| [accounts/](../../backend/accounts/) | Roles, institutions, teams, access and audit events |
| [projects/](../../backend/projects/) | Projects, datasets, membership and sharing |
| [volumes/](../../backend/volumes/) | Registration, metadata, ROI, pyramids and chunk reads |
| [annotation/](../../backend/annotation/) | Tasks, edits, submission/review, hard cases, AI and measurements |
| [processing/](../../backend/processing/) | Durable processing jobs and dispatcher |
| [core/](../../backend/core/) | Shared types, storage boundaries, security and deployment helpers |

`models.py` defines database objects, serializers define API representations,
and `migrations/` records database evolution. Voxel arrays are not stored in
these tables; see [data and storage](data-and-storage.md) for ownership and lifecycle.

## Find a feature

| Question | Start with |
| --- | --- |
| How do Home tasks and Review controls work? | [HomePage](../../frontend/src/pages/HomePage.tsx), [WorkList](../../frontend/src/components/WorkList.tsx) |
| How do dual roles and manager grants work? | [Roles and workspaces](roles-and-workspaces.md), [accounts/roles.py](../../backend/accounts/roles.py), [authentication.py](../../backend/accounts/authentication.py) |
| How do I add or disable a research tool? | [Project extension guidelines](extensions.md) |
| How do measurements use spacing? | [Measurements walkthrough](feature-walkthrough.md) |
| What distinguishes working labels, snapshots and official labels? | [label_paths.py](../../backend/annotation/label_paths.py), [annotation services](../../backend/annotation/services.py), [review guide](../user-guide/07-submit-and-review.md) |
| How does streaming improve viewing? | [pyramid/](../../backend/volumes/pyramid/), [architecture](architecture.md) |
| What behavior must changes preserve? | [Product invariants](../product-invariants.md), [AGENTS.md](../../AGENTS.md) |

Runtime directories remain stable so documentation organization does not break
imports, migrations, model paths or services. Potential module refactors are
recorded separately in the [software audit](software-audit.md).
