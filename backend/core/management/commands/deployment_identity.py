"""Print this instance's deployment identity.

    python manage.py deployment_identity          # human-readable
    python manage.py deployment_identity --json   # for scripting/diffing

Use during a cutover to prove that the process you are about to promote has
the data root and database you expect — *before* moving the ingress onto it.
Afterwards, compare this fingerprint against the one the public URL reports
(``GET /api/deployment/identity/``); they must match. See DEPLOYMENT.md.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from core.deployment import describe, identity


class Command(BaseCommand):
    help = "Show this instance's deployment identity (no secrets)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit JSON instead of the human-readable rendering.",
        )
        parser.add_argument(
            "--fingerprint",
            action="store_true",
            help="Emit only the fingerprint, for scripted comparison.",
        )

    def handle(self, *args, **options):
        info = identity()
        if options["fingerprint"]:
            self.stdout.write(str(info["fingerprint"]))
        elif options["json"]:
            self.stdout.write(json.dumps(info, indent=2, sort_keys=True))
        else:
            self.stdout.write(describe(info))
