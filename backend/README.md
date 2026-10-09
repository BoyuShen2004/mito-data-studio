# Backend reading guide

Django handles authorization, metadata, label persistence, review and processing.
Voxel files are stored separately from database records.

1. Find the request in [API routes](config/urls.py).
2. Follow its app's API, services, models and adjacent tests.
3. Trace the [Measurements example](../docs/engineering/feature-walkthrough.md).

The maintained documentation contains the [app map](../docs/engineering/code-map.md),
[data contract](../docs/engineering/data-and-storage.md) and
[development commands](../docs/development.md).

Run the full Django suite from this directory with
`python manage.py test --noinput` and check that discovery finds a nonzero number
of tests. Configure isolated development/test databases and data storage first.
Migration files record history; do not move or reorder them to tidy the repository.
