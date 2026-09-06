"""HTTP surface for the per-person inbox.

Read-and-acknowledge only: nothing here creates a notification. Notifications
are written by the service layer (see :mod:`accounts.notifications`) so that
what lands in an inbox is decided next to the action that caused it, never by
a client.

Returns 503 when ``FEATURE_NOTIFICATIONS`` is off, following the same
convention as the dashboards and chunk service. Recording continues while the
flag is off, so enabling it later reveals the history rather than starting from
an empty inbox.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings

from .notifications import inbox, mark_read, unread_count

_DISABLED = Response(
    {"detail": "Notifications are not enabled.", "reason": "disabled"},
    status=status.HTTP_503_SERVICE_UNAVAILABLE,
)

# Hard ceiling on one page, so a client cannot ask for an unbounded inbox.
MAX_LIMIT = 200


def notifications_enabled() -> bool:
    return bool(getattr(settings, "FEATURE_NOTIFICATIONS", False))


def serialize(row) -> dict:
    return {
        "id": row.pk,
        "verb": row.verb,
        "title": row.title,
        "body": row.body,
        "url": row.url,
        "actor": row.actor.get_username() if row.actor else None,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "read": row.is_read,
        "created_at": row.created_at.isoformat(),
    }


def _int_param(request, name, default, *, maximum=None):
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    value = max(0, value)
    return min(value, maximum) if maximum is not None else value


class NotificationListView(APIView):
    """``GET /api/notifications/?unread=1&limit=&offset=``."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not notifications_enabled():
            return _DISABLED
        unread_only = request.query_params.get("unread") in ("1", "true", "True")
        limit = _int_param(request, "limit", 50, maximum=MAX_LIMIT) or 1
        offset = _int_param(request, "offset", 0)
        rows = inbox(
            request.user, unread_only=unread_only, limit=limit, offset=offset
        )
        return Response(
            {
                "results": [serialize(row) for row in rows],
                "unread": unread_count(request.user),
                "limit": limit,
                "offset": offset,
            }
        )


class NotificationUnreadCountView(APIView):
    """``GET /api/notifications/unread-count/`` — the bell badge.

    Kept separate from the list because the frontend polls it on a timer; it
    must stay one indexed count and must never grow a join or a serializer.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not notifications_enabled():
            return _DISABLED
        return Response({"unread": unread_count(request.user)})


class NotificationMarkReadView(APIView):
    """``POST /api/notifications/read/`` with ``{"ids": [...]}`` or ``{}``.

    An omitted ``ids`` marks everything unread read. The update is scoped to
    ``recipient=request.user`` inside the same statement, so passing somebody
    else's ids marks nothing rather than marking their inbox read.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not notifications_enabled():
            return _DISABLED
        payload = request.data if isinstance(request.data, dict) else {}
        ids = payload.get("ids")
        if ids is not None and not isinstance(ids, list):
            return Response(
                {"detail": "ids must be a list of notification ids."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            changed = mark_read(request.user, ids)
        except (TypeError, ValueError):
            return Response(
                {"detail": "ids must be a list of integers."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"marked": changed, "unread": unread_count(request.user)})
