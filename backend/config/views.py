"""Project-level views.

The user-facing app is the React SPA (dev server on http://localhost:5173).
This Django process is the JSON API backend; visiting its root in a browser
would otherwise 404, so we serve a small landing page pointing to the UI,
the admin, and the API.
"""

from django.conf import settings
from django.http import HttpResponse

_LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mito Data Agent API</title>
  <style>
    body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
           max-width: 640px; margin: 8vh auto; padding: 0 1.25rem; color: #1c2430; }
    h1 { margin-bottom: .25rem; }
    .muted { color: #6b7280; }
    a { color: #2563eb; }
    ul { line-height: 1.9; }
    code { background: #f1f3f7; padding: .1rem .35rem; border-radius: 4px; }
    .note { background: #eef4ff; border: 1px solid #cfe0ff; border-radius: 8px;
            padding: .75rem 1rem; margin-top: 1.25rem; }
  </style>
</head>
<body>
  <h1>🧬 Mito Data Agent API</h1>
  <p class="muted">The backend is running. This server only provides the JSON
  API and admin &mdash; it is not the user interface.</p>
  <div class="note">
    <strong>Open the app here &rarr;</strong>
    <a href="http://localhost:5173/">http://localhost:5173/</a><br>
    Start it with <code>cd frontend &amp;&amp; npm run dev</code> if it isn't running.
  </div>
  <ul>
    <li><a href="/admin/">/admin/</a> &mdash; Django admin (internal debugging)</li>
    <li><a href="/api/">/api/</a> &mdash; REST API root (login required for most endpoints)</li>
    <li><code>POST /api/auth/login/</code> &mdash; obtain an auth token</li>
  </ul>
</body>
</html>
"""


def index(request):
    return HttpResponse(_LANDING_HTML)


def spa_index(request):
    """Serve the built React app (frontend/dist/index.html) — production only.

    Read fresh each request rather than cached at import time: gunicorn
    workers are long-lived, and this keeps a `npm run build` + reload simple
    (no need to also bounce every worker to pick up a new index.html).

    Always no-store: if this view is ever hit for a mistaken /assets/* URL,
    Cloudflare must not cache HTML under a .js path (that white-screens the SPA).
    """
    response = HttpResponse((settings.FRONTEND_DIST / "index.html").read_text())
    response["Cache-Control"] = "no-store, must-revalidate"
    # Helps confirm which build is live when debugging stale browsers/CDNs.
    try:
        response["X-Mito-Frontend-Build"] = str(
            int((settings.FRONTEND_DIST / "index.html").stat().st_mtime)
        )
    except OSError:
        pass
    return response
