# RC TLS, cookie, and HSTS decision

The intended public chain is:

`mito-data-agent.seg.bio -> Cloudflare edge -> remotely managed cloudflared -> loopback gunicorn`

Nginx is installed on the host but is not in this application's route. The
tunnel origin is loopback-only. Cloudflare overwrites `X-Forwarded-Proto` with
the visitor scheme, and the application may therefore trust that header at
this boundary. The forwarded Host header is not needed: cloudflared preserves
the public Host and Django validates it directly.

## Selected profiles

Private staging remains HTTP-only and loopback-only: proxy trust, SSL redirect,
secure cookies, and HSTS are off; allowed hosts and CSRF origins name only
`127.0.0.1:18189`/localhost.

Public production uses `ops/production/production-tls.env.example`: proxy
scheme trust, SSL redirect, secure session/CSRF cookies, and nosniff are on;
forwarded-host trust is off. HSTS starts at 300 seconds with neither
`includeSubDomains` nor preload. Those two deliberate deferrals are the only
expected Django deploy warnings (`security.W005`, `security.W021`). A longer
HSTS lifetime is a later operational decision after stable HTTPS observation.

The live public HTTP endpoint currently returns 200 rather than redirecting.
Before cutover, enable Cloudflare **Always Use HTTPS**, confirm HTTP redirects
at the edge, then start the private release with this production profile.
Cloudflare recommends performing this redirect at the edge. Django's redirect
remains defense in depth and does not loop because HTTPS requests arrive with
`X-Forwarded-Proto: https`.

Do not enable HSTS preload or `includeSubDomains`: readiness of every
`*.seg.bio` hostname has not been established, and preload is intentionally
difficult to reverse.
