# Installation and operations

Team members using an existing lab instance can start with
[getting started](../getting-started/README.md); they do not need to deploy a service.

| Situation | Documentation |
| --- | --- |
| Run the web application and database on a fresh host | [Docker deployment](docker.md) |
| Develop locally with live reload | [Development environment](../development.md) |
| Configure development deployment for CPU/GPU hardware | [Hardware-adaptive deployment](hardware-adaptive.md) |
| Maintain the existing lab production host | [Production-host runbook](production-host.md) |
| Review previously used hardware | [Reference hardware](reference-hardware.md); this is not a minimum specification |

`docker-compose.yml` runs the web application and database;
`docker-compose.dev.yml` starts only the development database;
`docker-compose.dev-stack.yml` runs the full development web/database stack.
Choose the appropriate file and keep deployment identities and storage separate.
Queued processing requires a separately started dispatcher.

Before production updates, check dependencies, migrations, backups and rollback.
Production configuration, database and `MITO_DATA_ROOT` remain separate from
development. A code update is not a copy of the entire development directory.
Measurements require a dispatcher accepting `measure_mito` and the measurement
dependencies; an older service restricted to `build_pyramid` will not run them.
Current Docker dependency profiles omit kimimaro.

See the [release checklist](../release/checklist.md) and
[validation history](../release/validation-history.md) for checks and dated evidence.
