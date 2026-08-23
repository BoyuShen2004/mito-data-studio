"""Permanent redirect from retired hostnames to the canonical one.

A deployment that gets renamed keeps answering on its old hostname for a while:
bookmarks, saved links, and other people's notes all still point there. Serving
the same application on both addresses indefinitely means the rename never
actually finishes, so this middleware retires the old names by redirecting them.

Two deliberate choices:

* **Only explicitly listed hosts redirect.** The rule is not "anything that
  isn't canonical", because ``ALLOWED_HOSTS`` also carries ``127.0.0.1`` and
  ``localhost``, which the health checks, the metrics scraper, and the runbook's
  own loopback smoke tests use. Redirecting those would turn every ``200`` in
  the deployment checklist into a ``301`` pointing at a name that only resolves
  through the public proxy.

* **Unsafe methods get 308, not 301.** Browsers and most clients downgrade a
  redirected ``POST`` to ``GET`` on a ``301``, which would silently turn a save
  into a read. ``308`` carries the same "permanent" meaning while preserving the
  method and body, so an API client still pointed at the old host fails loudly
  or succeeds correctly rather than quietly losing the request.
"""

from __future__ import annotations

from urllib.parse import urlunsplit

from django.conf import settings
from django.http import HttpResponsePermanentRedirect, HttpResponseRedirect


class _PermanentRedirectPreservingMethod(HttpResponseRedirect):
    """A 308: permanent, but the client must not turn POST into GET."""

    status_code = 308


class CanonicalHostRedirectMiddleware:
    """Redirect retired hostnames to ``MITO_CANONICAL_HOST``.

    Inert unless both a canonical host and at least one legacy host are
    configured, so development and Docker deployments are unaffected.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.canonical = (getattr(settings, "MITO_CANONICAL_HOST", "") or "").strip()
        self.legacy = {
            host.strip().lower()
            for host in getattr(settings, "MITO_LEGACY_HOSTS", [])
            if host.strip()
        }
        # Never redirect the canonical host to itself, whatever the config says.
        self.legacy.discard(self.canonical.lower())

    def __call__(self, request):
        if self.canonical and self.legacy:
            redirect = self._redirect_for(request)
            if redirect is not None:
                return redirect
        return self.get_response(request)

    def _redirect_for(self, request):
        # get_host() is already validated against ALLOWED_HOSTS by this point;
        # split the port off so ``old.example:443`` matches ``old.example``.
        host = request.get_host().split(":")[0].lower()
        if host not in self.legacy:
            return None

        scheme = "https" if request.is_secure() else request.scheme
        location = urlunsplit(
            (scheme, self.canonical, request.path, request.META.get("QUERY_STRING", ""), "")
        )
        if request.method in ("GET", "HEAD"):
            return HttpResponsePermanentRedirect(location)
        return _PermanentRedirectPreservingMethod(location)
