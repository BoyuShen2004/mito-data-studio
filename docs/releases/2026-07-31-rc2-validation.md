# RC2 release-gate validation

Validated application/config tip before this evidence commit:
`1b6a70099edcf969d403a95a12ad8249cd877235`. Parent development branch:
`webknossos-transformation`. This pass prepares a local RC2 only; it does not
push, deploy, change public ingress, or create a production tag.

## Process cleanup and staging identity

One stale polling shell from the earlier release run was terminated. No stale
Playwright, Chromium, Vite, watcher, test server, soak worker, or test database
remains. The only surviving application services are intentional staging and
production services.

Staging remains isolated at `127.0.0.1:18189`, database `mito_staging` on
5434, and its dedicated data root. A staging-only restart changed its master
PID from 3241538 to 3826705; health and restored browser smoke passed after
reconnection. Live 18188 remained master PID 2935215, started 2026-07-29
13:48:30 EDT, and returned HTTP 200.

## Soak gate

The old 300-second failure was a harness artifact: it took its baseline before
lazy route allocation, sampled one periodic manager/editor route phase against
another, and treated file-backed TIFF mmap pages as ordinary RSS. Commit
`2abfa80` fixed request and PSS metrics; commit `35b6591` makes browser sampling
route-phase-stable and stops active TIFF decoders before closing contexts.

The authoritative production-build/gunicorn/TIFF run used two distinct
restored non-privileged users in independent Chromium contexts for the full
7,200 seconds after warmup:

- 5,131 navigation cycles and 232 GC-assisted browser samples;
- 105,924 HTTP 200 plus 432 HTTP 304 responses, zero visible failures and zero
  chunk requests;
- 2,875 expected `net::ERR_ABORTED` superseded TIFF prefetches;
- response p50/p95/p99 32.74/293.88/453.84 ms;
- combined two-user render-cycle p50/p95/p99
  1203.41/2345.89/2630.24 ms;
- event-loop lag p50/p95/p99/max 0.10/9.60/192.50/338.40 ms;
- user 1 first-to-last quarter heap median +0.57%, p95 +0.43%;
- user 2 median +0.11%, p95 +0.04%;
- 239 system samples, three workers throughout, average 6.99 PostgreSQL
  connections, max receive queue zero, anonymous PSS range 3.58..3.72 GiB.

The original command retained exit status 1 because its old teardown timed out
after all 7,200 seconds and emitted an artifact ZIP error. It is not relabeled
as a passing command. The fixed harness then ran a separate 300-second
confirmation: 355 cycles, zero failures, phase-stable heap +17.10%/+6.45%, and
normal teardown, **1/1 passed**. Together, the complete long-run measurements
and passing corrected gate demonstrate bounded memory and cleanup.

Real restored-data writes bracketed the soak: two distinct-task edits were
attempted and preserved before and after; same-task stale writes returned
controlled 409 without changing the mask. Four source/official-reference files
were SHA-256 identical before and after. Only the two staging working masks
changed through explicit tests.

## Dependencies, TLS, and offline models

Vite is pinned to patched `6.4.3`; the former high finding is gone. Final
`npm audit` has zero high/critical and two moderate vulnerable packages. Those
package findings contain three React Router advisories, assessed in
`2026-07-31-dependency-audit.md`; the application has neither SSR hydration nor
an attacker-controlled navigation target. The unsafe Router 7.18 trial was not
adopted because that tested version introduced a newer high RSC finding.

The public profile is defined in `2026-07-31-tls-decision.md`: trust
Cloudflare's overwritten `X-Forwarded-Proto`, enable edge Always Use HTTPS and
Django SSL redirect/secure cookies at cutover, start HSTS at 300 seconds, and
leave includeSubDomains/preload false. Deploy check reports only the two
intentional W005/W021 warnings. Public ingress was not changed.

All three required LFS objects are present in the protected offline bundle and
match their pointer OIDs/sizes. In a network-disabled namespace, EfficientSAM
encoder/decoder ONNX sessions and the SAM2.1 large CPU video predictor loaded
successfully. The source projects' Apache-2.0 provenance is recorded.

## Correctness and automated gates

- PostgreSQL backend: **1230/1230**, 1117.850 s, disposable DB destroyed;
- TypeScript clean; Vitest **151/151**; production build passed with Vite 6.4.3;
- Phase 14 Chromium **5/5**; 512² p95 28.2 ms, synthetic 2048² p95 146 ms,
  retained heap +0.85%;
- restored default browser smoke **3/3** after staging restart, with eight
  explicit optional/write/chunk cases skipped by gates;
- explicit real write/conflict tests **2/2** before and after the soak;
- Django system check clean, makemigrations no changes, restored migrate plan
  empty, production deploy profile only W005/W021;
- npm audit: 0 high, 0 critical, 2 moderate package findings.

The real restored 2048² chunk path remains materially slower than TIFF
(p95 about 3382 versus 473 ms; 370 requests/96.5 MB versus 22/26.8 MB). Its
full-plane overfetch is documented and `VITE_FEATURE_CHUNK_RENDERER=false`
remains mandatory for this release.

## Cutover rehearsal

Evidence: `/home/weidf/shenb/mito-cutover-rehearsal-20260731T2035Z`.
Using staging as the old side and a separate restored DB/data root as the new
side:

- PG16 dump/validation 1 s; 13 GiB data clone 42 s; restore 8 s;
- freeze rejected POST with 503 while GET remained 200;
- local proxy route switch 35 ms; rollback freeze 13 ms; rollback switch 37 ms;
- first Save exposed a disposable non-root Nginx body-temp permission error
  before Django; configuring a protected `client_body_temp_path` fixed it;
- authenticated Brush/Save/reload then passed 1/1;
- old working mask remained byte-identical while only the new mask changed;
- rollback restored old-service read health; live 18188 stayed untouched.

Disposable proxy/gunicorn processes and the rehearsal DB were removed. The
protected evidence, archive and data clone remain outside Git.

## Recommendation

The TIFF-based RC2 is a **go as a local release candidate**. Production
cutover remains **no-go in this pass** until a declared maintenance window:
enable and verify Cloudflare Always Use HTTPS, enforce the real write freeze,
take/validate the final backup, restore to a new production identity, apply the
prepared TLS environment, run private authenticated Save/reload, and only then
switch ingress. Keep the chunk renderer false. Roll back immediately on any
identity mismatch, source mutation, silent edit loss, mask corruption,
permission expansion, repeated 5xx, or failed Save/reload.
