# Prepared production cutover and rollback (do not execute yet)

This run stops before cutover. The commands below are the reviewed operator
procedure for a later maintenance window. Run them interactively, one section
at a time, after replacing `RC_COMMIT` only with the approved/tagged commit.
Never reuse the mutated staging DB/data root as production.

## Preconditions

```bash
export RC_COMMIT=$(git -C /home/weidf/shenb/mito-data-agent \
  rev-parse release/mito-data-agent-2026-07-31-rc2^{commit})
export CUTOVER_TS=$(date -u +%Y%m%dT%H%M%SZ)
export CUTOVER_ROOT=/home/weidf/shenb/mito-cutover-$CUTOVER_TS
export NEW_CHECKOUT=/home/weidf/shenb/mito-data-agent-release-$CUTOVER_TS
export NEW_DATA_ROOT=/home/weidf/shenb/mito-data-agent-production-data-$CUTOVER_TS
install -d -m 0700 "$CUTOVER_ROOT"
test "$(git -C /home/weidf/shenb/mito-data-agent rev-parse "$RC_COMMIT^{commit}")" = "$RC_COMMIT"
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:18188/login)" = 200
```

Record the existing Cloudflare config/service and 18188 PID/start time in
`$CUTOVER_ROOT` before changing anything. Announce maintenance, then enforce a
write freeze at ingress/application policy and verify a real authenticated
write is rejected. A banner alone is not a freeze.

## Final backup under the write freeze

Load the protected live `.env` only into the shell; never print it. Use the
PG16 client in `mito-dev-postgres`, matching the server major version.

```bash
set -a; source /home/weidf/shenb/mito-data-agent-deploy/.env; set +a
docker exec -e PGPASSWORD="$MITO_DB_PASSWORD" mito-dev-postgres \
  pg_dump -U "$MITO_DB_USER" -d "$MITO_DB_NAME" -Fc \
  > "$CUTOVER_ROOT/production-final.pgdump"
test -s "$CUTOVER_ROOT/production-final.pgdump"
docker exec -i mito-dev-postgres pg_restore -l \
  < "$CUTOVER_ROOT/production-final.pgdump" \
  > "$CUTOVER_ROOT/production-final.toc"
test -s "$CUTOVER_ROOT/production-final.toc"
tar --create --file "$CUTOVER_ROOT/production-data-final.tar" \
  --directory "$MITO_DATA_ROOT" .
test -s "$CUTOVER_ROOT/production-data-final.tar"
(cd "$MITO_DATA_ROOT" && find . -type f -print0 | sort -z | xargs -0 sha256sum) \
  > "$CUTOVER_ROOT/production-data-final.sha256"
```

Also capture critical row counts and verify they match immediately before and
after restore. Keep backups mode 0600 and outside every repository.

## Build the clean production release privately

```bash
git clone --no-local /home/weidf/shenb/mito-data-agent "$NEW_CHECKOUT"
git -C "$NEW_CHECKOUT" switch --detach "$RC_COMMIT"
python3.11 -m venv "$NEW_CHECKOUT/venv"
"$NEW_CHECKOUT/venv/bin/pip" install --require-hashes \
  -r "$NEW_CHECKOUT/requirements-release.txt"
(cd "$NEW_CHECKOUT/frontend" && npm ci && \
  VITE_FEATURE_CHUNK_PULL_QUEUE=false VITE_FEATURE_CHUNK_RENDERER=false npm run build)
install -d -m 0750 "$NEW_DATA_ROOT"
tar --extract --file "$CUTOVER_ROOT/production-data-final.tar" \
  --directory "$NEW_DATA_ROOT"
```

Create a new dedicated production DB, restore the custom archive into it, run
`reconcile_legacy_region_mask_migration` as a dry run (repeat with `--apply`
only if it reports the exact known schema), then `migrate --plan`, `migrate`, `check`, and
`check --deploy`. Create a protected release `.env` with the new DB/data root,
`DEBUG=False`, all Phase flags off, and both Vite flags false. Do not reuse the
staging SECRET_KEY or database credentials.

Apply the public TLS profile from `ops/production/production-tls.env.example`.
Enable Cloudflare Always Use HTTPS and verify the edge redirect before enabling
Django's SSL redirect. Keep HSTS at the initial 300 seconds with
includeSubDomains/preload off. If a non-root local Nginx proxy is used for a
rehearsal, give it an explicit writable `client_body_temp_path`; autosave
payloads are large enough to spill from memory. The actual production route is
cloudflared directly to loopback gunicorn and does not use that disposable
Nginx layer.

Start a new dedicated systemd service privately on `127.0.0.1:18190`. Verify
the authenticated identity chain (18190 → new PID → `RC_COMMIT` → new DB →
`NEW_DATA_ROOT`) and run login, project, TIFF axis, Save/reload, public share,
People/Hard Cases and configured AI smoke tests. Hash the old live mask before
and after the 18190 Save: only `NEW_DATA_ROOT` may change.

## Ingress switch and verification

Only after the private gate is green, change the protected Cloudflare ingress
service mapping from `http://127.0.0.1:18188` to
`http://127.0.0.1:18190`, validate its configuration, and reload only
`cloudflared-demo-seg-bio.service`. Do not stop 18188.

Verify publicly: deployment identity/commit, login, project/resume, read-only
share, TIFF XY/XZ/YZ, authenticated Save/reload, People/Hard Cases, and AI
failure safety. Confirm old masks do not change. Monitor both services, DB
connections, 4xx/5xx, worker RSS and Save latency for at least the agreed
observation window. Keep the chunk renderer false.

Rollback immediately on any silent edit loss, source mutation, wrong identity
chain, migration inconsistency, repeated 5xx, permission expansion, mask hash
corruption, or inability to Save/reload.

## Rollback

1. Freeze writes again.
2. Point the protected Cloudflare mapping back to
   `http://127.0.0.1:18188`, validate, and reload only the tunnel service.
3. Confirm PID/start/HEAD and authenticated Save/reload on the old service.
4. If no writes reached the new service, retain its DB/data for forensics and
   end rollback here.
5. If new writes must be preserved, do not copy masks ad hoc. Export the new
   DB/data, reconcile under an explicit maintenance migration, and verify row
   counts/hashes before reopening writes.
6. If the old production DB/data was ever changed during cutover, restore the
   verified final custom archive and data tar to new versioned locations, point
   the old service at those restored locations, migrate only to its known
   schema, and verify hashes before reopening.

Never delete the failed release checkout, new DB, new data root, final backup,
or logs until the incident review is complete.
