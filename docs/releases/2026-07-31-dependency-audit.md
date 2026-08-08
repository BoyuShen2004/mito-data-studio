# RC dependency audit decision

## Vite / esbuild

The release now pins Vite `6.4.3`. The prior Vite 6 development-server
advisory was removed by this compatible backport; TypeScript, 151 Vitest tests,
the production build, route tests, and Chromium checks passed after the lock
update. Vite remains a development/build dependency. Neither staging nor the
planned production service starts `vite` or `vite preview`: gunicorn serves
the compiled `frontend/dist` files from the release checkout and binds only to
loopback.

This removes the former high finding rather than relying only on its
development-only exposure. The lock contains a single Vite `6.4.3` runtime for
the build/test toolchain.

## React Router

The installed declarative router is `react-router-dom` / `react-router`
`6.30.4`. `react-router-dom` is direct and `react-router` is its transitive
runtime dependency. `npm audit` groups the following three advisories into two
moderate vulnerable package findings (zero high/critical):

- `GHSA-wrjc-x8rr-h8h6`, open redirect via backslash in `Link`/`navigate`,
  affects React Router `>=6.0.0 <7.18.0`;
- `GHSA-337j-9hxr-rhxg`, constructor injection in SSR hydration
  `deserializeErrors`, affects `>=6.4.0 <7.18.0`;
- `GHSA-jjmj-jmhj-qwj2`, open redirect leading to XSS in
  `react-router-dom`, affects `>=6.30.2 <=6.30.4`.

The available patched line is React Router 7.18, so it is not a patch-only
release update.

An isolated compatibility trial with `7.18.0` passed TypeScript, all 151
Vitest tests, and the production build, but that version was itself in the
affected range of a newer high-severity React Server Components advisory at
the time of audit. Moving the release merely to exchange two understood
moderate findings for a new high finding is not acceptable.

The current application uses declarative `BrowserRouter`, `Routes`, `Route`,
`Navigate`, `Link`, and navigation hooks. It does not use React Router server
rendering, RSC routes, framework/data-router server handlers, or user-provided
redirect destinations. Every navigation target is a fixed local path or a
local path assembled from numeric IDs; the only API-returned `Link` target is
the server-generated `/hard-cases/<id>` path, covered by a backend assertion.
Routes and API authorization are fixed by application code; Django enforces
permissions independently of client routing. This makes the hydration issue
absent and removes the attacker-controlled destination prerequisite for both
redirect issues in the deployed topology.

Decision: retain `6.30.4` for this TIFF release, record the two moderate
findings as accepted with the controls above, and perform the Router 7 move
only when a release outside all current advisory ranges is available. Do not
use `npm audit fix --force`.

The final release audit must remain at zero high/critical findings. Any change
to SSR, RSC, data-router actions, or user-controlled redirect behavior reopens
this acceptance immediately.
