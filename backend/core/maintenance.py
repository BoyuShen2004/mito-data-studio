from django.conf import settings
from django.http import JsonResponse


class MaintenanceWriteFreezeMiddleware:
    """Reject new business mutations during a controlled reset window."""

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
    ALLOWED_PREFIXES = ("/api/auth/", "/api/admin/reset/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            settings.MITO_MAINTENANCE_MODE
            and request.method not in self.SAFE_METHODS
            and not request.path.startswith(self.ALLOWED_PREFIXES)
        ):
            response = JsonResponse(
                {"detail": "Application maintenance is active; mutations are temporarily frozen."},
                status=503,
            )
            response["Retry-After"] = "60"
            return response
        return self.get_response(request)
