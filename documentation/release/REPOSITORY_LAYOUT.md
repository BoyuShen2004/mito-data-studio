# Release repository layout

The runtime layout is already coherent and is deliberately not moved during
documentation reorganization.

```text
backend/          Django application, migrations, services, tests
frontend/         React/TypeScript application and browser tests
documentation/    release, user, technical, deployment, and paper portal
docs/             established operational guides and product invariants
ops/              Docker entrypoint, hardware probe, staging/production/release assets
scripts/          developer setup and run automation
vendor/           Git LFS model assets and upstream license files
Dockerfile        multi-profile container build
docker-compose*.yml
environment.yml
requirements-release.*
README.md
CHANGELOG.md
CONTRIBUTING.md
SECURITY.md
THIRD_PARTY_NOTICES.md
LICENSE
```

Moving Django apps, frontend source, migrations, vendor paths, or host runbooks
for cosmetic reasons would risk breaking imports, model lookup, static builds,
service units, and reproducibility. Release readiness is achieved through a
stable public navigation layer, immutable tags, clean artifacts, tests, license
review, and documented deployment—not by renaming functioning source trees.

Existing `docs/` URLs remain valid. New publication-facing material lives in
`documentation/`; future new documents should be placed by audience and linked
from its root index.

