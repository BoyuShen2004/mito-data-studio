# Production v1.0.0 operator handoff

Production was cut over on 2026-07-31 from the retained deployment on port
18188 to the clean, tagged v1.0.0 release on port 18190. The release is the
annotated tag `mito-data-agent-v1.0.0`, commit
`a9865fd829c768ef7a6e68613cec08c6e96827af`. Git history and tags remain local.
The public route became active at 2026-07-31 21:48:22 EDT and passed its
mandatory observation gate at 2026-07-31 22:22:21 EDT.

## Active identity

```text
https://mito-data-agent.seg.bio
  -> cloudflared-demo-seg-bio.service
  -> http://127.0.0.1:18190
  -> mito-data-agent-v1.0.0.service
  -> /home/weidf/shenb/mito-data-agent-production-v1.0.0
  -> PostgreSQL database mito_production_v1_0_0
  -> /home/weidf/shenb/mito-data-agent-production-data-v1.0.0
```

The dedicated service account is `mito-production`. The service is confined
to its own data, log, and runtime directories; the external source/reference
tree is read-only. The production renderer remains TIFF/PNG. Both
`VITE_FEATURE_CHUNK_RENDERER` and `VITE_FEATURE_CHUNK_PULL_QUEUE` are false.

Production database paths retain the historical `/home/weidf/shenb/wk_data`
prefix. That path is a compatibility symlink to the consolidated authoritative
tree at `/home/weidf/shenb/data/wk_data`; do not remove it without first
migrating every persisted volume/dataset path and updating the systemd
read-only-path policy.

After the post-release workspace cleanup, the predecessor checkout and staging
deployment were removed and ports 18188/18189 were closed. The predecessor
database remains outside this workspace. The final file-level database/data
backup was subsequently deleted by explicit operator request; there is no
complete retained filesystem rollback set.

## Routine operation

```bash
# Health and identity
curl -fsS https://mito-data-agent.seg.bio/health/
sudo systemctl status mito-data-agent-v1.0.0.service

# Logs
sudo journalctl -u mito-data-agent-v1.0.0.service --since today
sudo tail -f /home/weidf/shenb/mito-data-agent-production-v1.0.0/logs/error.log
sudo tail -f /home/weidf/shenb/mito-data-agent-production-v1.0.0/logs/access.log

# Controlled lifecycle
sudo systemctl start mito-data-agent-v1.0.0.service
sudo systemctl reload mito-data-agent-v1.0.0.service
sudo systemctl restart mito-data-agent-v1.0.0.service
sudo systemctl stop mito-data-agent-v1.0.0.service
```

Before any reload or restart, verify that no Save/autosave request or database
write transaction is in flight. Never run a development Vite server in the
production topology.

## Backup

Load the protected production environment into the current shell without
printing it. Use the PostgreSQL 16 client in `mito-dev-postgres` and create the
backup outside all checkouts:

```bash
export BACKUP_ROOT=/home/weidf/mito-production-backup-$(date -u +%Y%m%dT%H%M%SZ)
install -d -m 0700 "$BACKUP_ROOT"
set -a
source /home/weidf/shenb/mito-data-agent-production-v1.0.0/.env
set +a
docker exec -e PGPASSWORD="$MITO_DB_PASSWORD" mito-dev-postgres \
  pg_dump -U "$MITO_DB_USER" -d "$MITO_DB_NAME" -Fc \
  > "$BACKUP_ROOT/production.pgdump"
test -s "$BACKUP_ROOT/production.pgdump"
docker exec -i mito-dev-postgres pg_restore -l \
  < "$BACKUP_ROOT/production.pgdump" > "$BACKUP_ROOT/production.toc"
test -s "$BACKUP_ROOT/production.toc"
(cd "$MITO_DATA_ROOT" && find . -type f -print0 | sort -z | xargs -0 sha256sum) \
  > "$BACKUP_ROOT/data.sha256"
```

No file-level production backup is currently retained. Before any upgrade,
migration, service replacement, or other risky operation, run and validate the
procedure above and also copy the working data root into a separate protected
location. The required model weights remain as verified full bytes in the
development Git LFS cache and active production checkout.

## Rollback

Rollback is mandatory for wrong-root writes, missing edits after reload,
source/reference mutation, database integrity errors, repeated HTTP 500s,
blocking authentication/CSRF errors, redirect loops, mask corruption,
permission regressions, or identity mismatch.

An immediate complete rollback is not currently available: port 18188, its
checkout/data tree, and the final file backup were removed. If the active
service fails, first freeze writes and preserve the current 18190 database,
data root, configuration, and logs. Reconstruct code from the local production
tag, but do not claim data rollback unless a newly created, verified database
and working-data backup exists. The predecessor PostgreSQL database alone is
not sufficient because its matching working-mask tree is no longer retained.

## TLS and models

Cloudflare terminates TLS and overwrites the trusted forwarded-protocol header.
Django enables SSL redirect and secure session/CSRF cookies. Initial HSTS is
300 seconds, without subdomains or preload. Do not lengthen HSTS or enable
preload without a separate domain-wide review.

Offline assets and expected hashes are recorded in the protected release
asset manifest. EfficientSAM encoder/decoder and SAM2.1 large were loaded and
executed locally without network access before cutover. Model binaries are
operational assets and must not be committed to normal Git history.

## Cutover record

Private and public authenticated Brush/Save/reload tests verified that only
the new data root and new database changed. The old mask/metadata inventory and
all five external source/reference files matched their frozen SHA-256 hashes.
Two preparatory cutover attempts were rolled back to 18188 after migration
ordering checks failed safely. The successful migration sequence was:

1. `processing.0002_phase11_build_pyramid_job_type`;
2. `volumes.0005_phase11_volume_pyramid`;
3. `reconcile_legacy_region_mask_migration --apply`;
4. the remaining normal migration plan.

No migration was applied to the old production database. The detailed cutover
artifact directory was removed during the requested post-release cleanup; the
validated outcome and observation summary remain recorded in this document.

The 30-minute public observation comprised 60 probes: 60 HTTP 200 responses,
latency p50/p95/p99 89.459/99.553/122.136 ms, one unchanged master PID, all
service checks active, zero error-log lines, and no worker restart. The access
window contained 62 HTTP 200 responses and one expected HTTP-to-HTTPS 301.
Database connections ranged from zero to four and returned to zero. Summed RSS
was constant; the post-model-load process set measured approximately 3.3 GiB
PSS. No rollback condition occurred during the active cutover.
