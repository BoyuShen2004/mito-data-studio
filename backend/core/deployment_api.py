"""HTTP surface for deployment identity.

One authenticated, read-only endpoint::

    GET /api/deployment/identity/

Why this must be reachable **over the public URL**, not just from a shell: the
question it answers is "which instance do my edits actually reach?", and a
shell on a host can only ever tell you about the process you already found.
Asking the public hostname makes the whole chain — DNS, tunnel, nginx, port,
process, checkout, database, data root — answer for itself.

Authenticated deliberately. The payload contains no credentials (see
``core.deployment.identity``), but filesystem paths and database names are
infrastructure detail with no reason to be anonymous. Any logged-in user may
read it: during a cutover the person holding a token is the person who needs
the answer, and gating it to staff would mean the smoke test cannot be run by
the annotator whose save is being verified.
"""

from __future__ import annotations

from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.deployment import identity


class DeploymentIdentityView(APIView):
    """Report this instance's identity and fingerprint."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(identity())


class DeploymentReleaseView(APIView):
    """Expose only the configured release label for the signed-out shell."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"release": identity()["service"]["release"]})
