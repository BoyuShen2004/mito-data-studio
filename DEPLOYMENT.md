# This-host systemd deployment

> This is a maintainer-specific runbook for the current production host. New
> installations should use the portable [Docker deployment](DOCKER.md).

The host runs the same application source as the canonical repository, but
keeps its environment, virtualenv, logs, process state, database, and volume
data local.

| Item | Current value |
| --- | --- |
| Checkout | `/home/weidf/shenb/mito-data-studio-production-v1.1.5` |
| Service user | `mito-production-v11` |
| Web unit | `mito-data-studio-v1.1.5.service` |
| Pyramid dispatcher | `mito-data-studio-v1.1.5-dispatcher.service` |
| Gunicorn bind | `127.0.0.1:18191` |
| Python environment | `<checkout>/venv` |
| Writable data root | `<checkout>/var/data` |
| Runtime directories | `<checkout>/logs`, `<checkout>/run`, `<checkout>/var` |

The live `.env` is mode-restricted and gitignored. Never print, copy, commit,
or replace it during source alignment. The systemd sandbox reads the checkout
and source-data roots but writes only the configured production data, log, and
run directories.

## Inspect and operate

```bash
sudo systemctl status mito-data-studio-v1.1.5.service
sudo systemctl status mito-data-studio-v1.1.5-dispatcher.service
sudo journalctl -u mito-data-studio-v1.1.5.service -n 100 --no-pager
```

Use a graceful reload after source/static changes:

```bash
sudo systemctl reload mito-data-studio-v1.1.5.service
```

Use restart—not reload—after an environment or systemd-unit change because a
gunicorn HUP does not reread `EnvironmentFile`:

```bash
sudo systemctl restart mito-data-studio-v1.1.5.service
```

Do not reload or restart unrelated units on other ports.

## Promote aligned source

Canonical development is the product source of truth. Before promotion:

1. Compare application paths while excluding `.env`, `venv/`, `var/`,
   `logs/`, `run/`, `frontend/dist/`, caches, and databases.
2. Port a production-only bugfix into canonical first; never preserve two
   feature variants.
3. Copy only reviewed application, test, Docker, and shared documentation
   changes into this checkout.
4. Never synchronize or delete runtime directories.

If Python dependencies or migrations changed:

```bash
cd /home/weidf/shenb/mito-data-studio-production-v1.1.5
venv/bin/pip install -r requirements-release.txt
cd backend
../venv/bin/python manage.py migrate --noinput
../venv/bin/python manage.py collectstatic --noinput
```

Migrations must be additive and reviewed. Back up first; never reset or recreate
the production database as an update shortcut.

If the frontend changed, build the profile that matches the production backend:

```bash
cd /home/weidf/shenb/mito-data-studio-production-v1.1.5
npm run build:production --prefix frontend
```

Then reload the web unit and smoke-check it. The dispatcher needs a restart only
when its Python code or unit definition changed.

## Health and identity checks

```bash
curl -fsS http://127.0.0.1:18191/healthz
curl -fsS http://127.0.0.1:18191/readyz
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18191/
```

When testing through the TLS proxy, include the forwarded protocol header for a
direct loopback request; otherwise the production HTTPS policy can redirect:

```bash
curl -fsS -H 'X-Forwarded-Proto: https' \
  -H 'Host: mito-data-studio.seg.bio' \
  http://127.0.0.1:18191/healthz
```

For a promotion or routing change, compare the authenticated public deployment
identity with the checkout's identity:

```bash
cd /home/weidf/shenb/mito-data-studio-production-v1.1.5/backend
../venv/bin/python manage.py deployment_identity
```

The chain to verify is:

```text
public URL -> reverse proxy -> :18191 -> v1.1.1 checkout -> production DB -> production MITO_DATA_ROOT
```

A successful localhost response does not prove that the public hostname reaches
the same instance.

## Streaming-pyramid dispatcher

The dispatcher runs `build_pyramid` jobs separately from gunicorn so data
registration stays responsive. View and Annotate fall back to original source
files until a pyramid validates successfully.

```bash
sudo systemctl restart mito-data-studio-v1.1.5-dispatcher.service
sudo journalctl -u mito-data-studio-v1.1.5-dispatcher.service -n 100 --no-pager
```

For region masks registered before region pyramids existed, use the idempotent
backfill command—first as a dry run:

```bash
cd /home/weidf/shenb/mito-data-studio-production-v1.1.5/backend
../venv/bin/python manage.py backfill_region_pyramids --dry-run
../venv/bin/python manage.py backfill_region_pyramids
```

The source image and region files remain immutable. Do not delete pyramids or
registered sources as a deployment step.

## Data-safety rules

- Annotation tools never persist implicitly. Only explicit Save writes pending
  slices to the working draft.
- Save acknowledgements are revision-specific; a concurrent newer edit remains
  pending.
- The working draft—not the official label—is the source for further work.
  Approval is what updates the official label.
- Whole-volume plans are bounded by the configured tool/track voxel limits.
- Never wipe or repoint `MITO_DATA_ROOT`, labels, region masks, pyramids, or the
  database during a code deployment.
- A production Save smoke changes annotator data. Perform it only with explicit
  authorization and an agreed test task; routine deployment smoke checks here
  are read-only.

## Backup and rollback

Back up both the PostgreSQL database and the production data root before a
schema change or cutover. Verify that the database dump contains table data and
that the data-root backup includes working masks. Keep rollback code, database,
and data-root versions as one coherent set.

If a new release is unhealthy, restore routing/code without allowing concurrent
writes to two copies of the working data. Never point the old and new processes
at different writable drafts while annotators continue working.
