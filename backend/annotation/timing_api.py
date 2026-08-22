"""HTTP surface for automatic annotation time tracking.

Four small endpoints for the editor (start / heartbeat / stop / status) and one
report for managers. They are deliberately in their own module rather than
appended to ``annotation/api.py``: every one of them must stay cheap enough to
call every thirty seconds, and none of them may ever touch a label volume. Kept
next to the label endpoints they would sooner or later grow a convenience
import that does.

Permissions, restated because they are the point:

* the **actor is always ``request.user``**. A client cannot time on behalf of
  anyone, and the session it names must already belong to it;
* only the **assigned annotator** of an **eligible** volume accrues time, and
  only while they may actually paint — managers, requesters, read-only viewers
  and the Details page all count for nothing;
* the cross-person report is manager-only, except that anyone may read their
  own. Timing data says when a named person was working, so it is not something
  to hand out more widely than the roster already is.

Nothing here returns an error the editor is expected to act on. A task that is
not being timed answers ``200`` with ``tracking: false`` and a reason, because
"we are not counting this" is a normal state, not a failure.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.roles import is_manager

from . import timing
from .models import AnnotationTask, WorkSession

User = get_user_model()


def _task(pk) -> AnnotationTask:
    return get_object_or_404(
        AnnotationTask.objects.select_related("volume", "project"), pk=pk
    )


def _status_payload(task, user, *, session=None) -> dict:
    """The one response shape every timing endpoint returns.

    A single schema means the client can treat start, heartbeat and status
    identically and simply store the latest answer — there is no combination of
    responses that leaves it guessing whether it is being timed.
    """
    allowed, reason = timing.can_track_task(user, task)
    summary = timing.task_time(task)
    return {
        "task_id": task.id,
        "volume_id": task.volume_id,
        "tracking": bool(allowed and session is not None and session.is_open),
        "eligible": timing.task_is_eligible(task),
        "reason": reason,
        "session_id": str(session.id) if session is not None else None,
        # Cumulative across every annotator, session, submit and reopen — this
        # is the number the Details row shows.
        "total_seconds": summary["seconds"],
        "display": summary["display"],
        "config": timing.timing_config(),
    }


class TaskTimingStatusView(APIView):
    """``GET /api/tasks/<id>/timing/`` — am I being timed, and what is the total?

    Read-only and side-effect free: opening the Details view or the read-only
    viewer must never start a clock, so this deliberately cannot.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from .services import can_view_task

        task = _task(pk)
        if not can_view_task(request.user, task):
            return Response(
                {"detail": "You do not have access to this task."},
                status=status.HTTP_403_FORBIDDEN,
            )
        session = None
        session_id = request.query_params.get("session_id")
        if session_id:
            session = WorkSession.objects.filter(
                pk=session_id, task=task, actor=request.user
            ).first()
        return Response(_status_payload(task, request.user, session=session))


class TaskTimingStartView(APIView):
    """``POST /api/tasks/<id>/timing/start/`` — start or resume this tab's session.

    Body: ``{"client_token": "<stable per-tab id>"}``. The token makes the call
    idempotent: a retry, a reconnect, or a refresh that reuses the tab's stored
    token resumes the same session instead of opening a second one. A genuinely
    different tab gets its own session, and the overlap between them is resolved
    at aggregation, where it can be resolved correctly.

    An ineligible task is **not** an error — it answers ``200`` with
    ``tracking: false`` so the editor simply does not start a heartbeat loop.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = _task(pk)
        allowed, reason = timing.can_track_task(request.user, task)
        if not allowed:
            return Response(_status_payload(task, request.user))
        try:
            session = timing.start_timing(
                task=task,
                actor=request.user,
                client_token=str(request.data.get("client_token") or ""),
            )
        except timing.TimingError as exc:
            return Response(
                {**_status_payload(task, request.user), "reason": exc.reason}
            )
        return Response(_status_payload(task, request.user, session=session))


class TaskTimingHeartbeatView(APIView):
    """``POST /api/tasks/<id>/timing/heartbeat/`` — "still here".

    Body: ``{"session_id": "<uuid>"}``. Credits the elapsed interval from the
    **server** clock; the request carries no duration and none would be believed.

    Re-checks permission on every beat, so losing the assignment or having the
    task locked stops accumulation within one cadence rather than at the next
    page load.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = _task(pk)
        session_id = request.data.get("session_id")
        if not session_id:
            return Response(
                {"detail": "session_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            session = timing.heartbeat_timing(
                session_id=session_id, actor=request.user, task=task
            )
        except timing.TimingError as exc:
            if exc.reason == "forbidden":
                return Response(
                    {"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN
                )
            # "gone", "closed", "not_assigned", "legacy_exempt", "not_editable":
            # all mean "stop heartbeating", none mean "something broke".
            return Response(
                {**_status_payload(task, request.user), "reason": exc.reason}
            )
        return Response(_status_payload(task, request.user, session=session))


class TaskTimingStopView(APIView):
    """``POST /api/tasks/<id>/timing/stop/`` — close this tab's session.

    Body: ``{"session_id": "<uuid>", "reason": "<optional>"}``. Idempotent: a
    client that stops twice, or whose ``sendBeacon`` raced its own route
    cleanup, is behaving correctly and gets ``200`` both times.

    Stopping never re-checks edit permission. Refusing to close a session
    because the annotator just lost the assignment would leave it open to be
    swept later instead of closed now, which is strictly worse.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = _task(pk)
        session_id = request.data.get("session_id")
        if not session_id:
            return Response(
                {"detail": "session_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            timing.stop_timing(
                session_id=session_id,
                actor=request.user,
                reason=str(request.data.get("reason") or "ended")[:32],
            )
        except timing.TimingError as exc:
            if exc.reason == "forbidden":
                return Response(
                    {"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN
                )
        return Response(_status_payload(task, request.user))


class AnnotatorTimeReportView(APIView):
    """``GET /api/people/<username>/time/`` — project → dataset → volume.

    Managers may read anyone's; everyone may read their own. Nobody else gets
    any of it: this is a record of when a named person was at their desk.

    One request returns the whole tree, already folded, so the People page can
    expand a project or a dataset without another round trip. It is built from a
    single interval query — see :func:`annotation.timing.annotator_time_report`.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        target = get_object_or_404(User, username=username)
        if target.id != request.user.id and not is_manager(request.user):
            return Response(
                {"detail": "Only managers can read another person's time."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(timing.annotator_time_report(target, viewer=request.user))
