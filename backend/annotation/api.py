from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.roles import is_annotator, is_manager
from accounts.teams import is_eligible_project_assignee
from core.choices import ACTIVE_TASK_STATUSES, TaskStatus
from core.permissions import IsAnnotator, IsManager
from projects.models import Project

from .models import AssignmentWithdrawal, AnnotationSubmission, AnnotationTask, HardCase
from .region_mask import request_roi_only
from .serializers import (
    AnnotationSubmissionSerializer,
    AnnotationTaskSerializer,
    AssignmentPlanSerializer,
    HardCaseSerializer,
    ReviewSerializer,
    SubmitInappTaskSerializer,
    SubmitTaskSerializer,
)
from .services import (
    apply_assignment_plan,
    apply_task_interpolation,
    apply_task_flood_fill,
    assign_task_to_annotator,
    auto_assign_project,
    can_annotate_task,
    can_edit_task,
    can_submit_task,
    can_take_down_hard_case,
    can_view_hard_case,
    can_view_task,
    can_view_volume,
    create_hard_case,
    get_public_hard_case,
    get_label_max_id,
    get_label_max_id_readonly,
    get_label_slice_ids,
    get_label_slice_ids_readonly,
    get_labels_3d_mesh,
    get_labels_3d_preview,
    get_labels_summary,
    get_region_label_ids,
    reset_working_labels_to_registered,
    get_visualization_state,
    latest_submission_ids,
    list_tracking_prompts,
    tracking_pending_review,
    list_assignment_plan_rows,
    plan_task_interpolation,
    plan_task_flood_fill,
    plan_delete_label_task,
    plan_merge_labels_task,
    plan_split_components_task,
    plan_watershed_task,
    plan_track_task_batch,
    predict_ai_mask,
    preview_assign_project,
    review_submission,
    set_hard_case_revoked,
    set_hard_case_status,
    set_label_lifecycle_action,
    set_label_slice_ids,
    set_task_annotation_lock,
    submit_annotation,
    submit_inapp_annotation,
    delete_tracking_prompt,
    replace_tracking_prompts,
    review_tracking_preview,
    upsert_tracking_prompt,
    visible_hard_cases,
    warm_ai_embedding,
)

User = get_user_model()


class ProjectTasksView(generics.ListAPIView):
    """List every task under a project. Managers only."""

    serializer_class = AnnotationTaskSerializer
    permission_classes = [IsManager]

    def get_queryset(self):
        qs = AnnotationTask.objects.filter(
            project_id=self.kwargs["project_id"]
        ).select_related("volume", "volume__dataset", "project", "assigned_to")
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs


class TaskDetailView(generics.RetrieveUpdateAPIView):
    """Retrieve or edit a task. Managers can edit; annotators see own tasks."""

    serializer_class = AnnotationTaskSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = AnnotationTask.objects.select_related(
            "volume", "volume__dataset", "project", "assigned_to"
        )
        if is_manager(self.request.user):
            return qs
        return qs.filter(assigned_to=self.request.user)

    def update(self, request, *args, **kwargs):
        if not is_manager(request.user):
            # Annotators may only move their own task into "in_progress".
            task = self.get_object()
            new_status = request.data.get("status")
            if new_status != TaskStatus.IN_PROGRESS:
                return Response(
                    {"detail": "Annotators may only start their tasks."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            task.status = TaskStatus.IN_PROGRESS
            task.save(update_fields=["status"])
            return Response(AnnotationTaskSerializer(task, context={"request": request}).data)
        return super().update(request, *args, **kwargs)


class AssignTasksView(APIView):
    """Auto-assign a project's volumes evenly across annotators. Managers only.

    Each volume becomes one whole-volume task (no frame splitting) and the tasks
    are balanced across active annotators. Requires a manager-reviewed project.
    """

    permission_classes = [IsManager]

    def post(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)
        summary = auto_assign_project(project, actor=request.user)
        if not summary.get("reviewed", True):
            return Response(summary, status=status.HTTP_400_BAD_REQUEST)
        return Response(summary)


class AssignmentPlanRowsView(APIView):
    """List a project's assignment-plan rows. Managers only.

    Ensures a whole-volume task per volume (so every volume shows up as an
    editable row) but never proposes annotators — see
    :class:`AssignmentPlanPreviewView` for that. This is what the plan editor
    loads on open, so a manager can start assigning without first clicking
    "Auto-fill balanced plan".
    """

    permission_classes = [IsManager]

    def post(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)
        summary = list_assignment_plan_rows(project)
        if not summary.get("reviewed", True):
            return Response(summary, status=status.HTTP_400_BAD_REQUEST)

        tasks = (
            AnnotationTask.objects.filter(project=project)
            .select_related("volume", "volume__dataset", "project", "assigned_to")
            .order_by("-priority", "created_at")
        )
        from .serializers import AssignmentPlanTaskSerializer
        rows = AssignmentPlanTaskSerializer(tasks, many=True).data
        return Response(
            {
                "created_tasks": summary["created_tasks"],
                "skipped_volumes": summary["skipped_volumes"],
                "entries": rows,
            }
        )


class AssignmentPlanPreviewView(APIView):
    """Return an editable assignment plan for a project. Managers only.

    Ensures a whole-volume task per volume, then proposes a balanced annotator
    for each unassigned task *without committing it*. The response lists every
    task (serialized) with an extra ``proposed_annotator_id`` the manager can
    accept or override before saving via :class:`AssignmentPlanApplyView`.
    """

    permission_classes = [IsManager]

    def post(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)
        summary = preview_assign_project(project)
        if not summary.get("reviewed", True):
            return Response(summary, status=status.HTTP_400_BAD_REQUEST)

        proposed = summary["proposed"]
        tasks = (
            AnnotationTask.objects.filter(project=project)
            .select_related("volume", "volume__dataset", "project", "assigned_to")
            .order_by("-priority", "created_at")
        )
        from .serializers import AssignmentPlanTaskSerializer
        rows = []
        serialized = AssignmentPlanTaskSerializer(tasks, many=True).data
        for task, data in zip(tasks, serialized):
            # Already-assigned tasks keep their annotator; unassigned ones get
            # the proposed pick (may be null when no annotator has capacity).
            data["proposed_annotator_id"] = proposed.get(
                task.id, task.assigned_to_id
            )
            rows.append(data)

        return Response(
            {
                "created_tasks": summary["created_tasks"],
                "skipped_volumes": summary["skipped_volumes"],
                "entries": rows,
            }
        )


class AssignmentPlanApplyView(APIView):
    """Commit a manager-edited assignment plan in one transaction. Managers only.

    Accepts ``{"entries": [{task_id, annotator_id?, priority?, difficulty?,
    instructions?, deadline?}, ...]}``. Reassignment updates tasks in place; a
    null/omitted ``annotator_id`` unassigns. Requires a manager-reviewed project,
    and every assignee must currently belong to a team granted to that project.
    """

    permission_classes = [IsManager]

    def post(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)
        if not project.manager_reviewed:
            return Response(
                {"detail": "Review the project before assigning its tasks."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AssignmentPlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entries = serializer.validated_data["entries"]

        # Validate every referenced annotator up front so the transaction only
        # runs on a fully-valid plan.
        annotator_ids = {
            e["annotator_id"]
            for e in entries
            if e.get("annotator_id") is not None
        }
        annotators_by_id = {}
        for uid in annotator_ids:
            user = get_object_or_404(User, pk=uid)
            if not is_annotator(user):
                return Response(
                    {"detail": f"User {user.username} is not an annotator."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not is_eligible_project_assignee(user, project):
                return Response(
                    {
                        "detail": (
                            f"User {user.username} is not a member of a team "
                            "eligible for this project."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            annotators_by_id[uid] = user

        try:
            summary = apply_assignment_plan(
                project,
                entries,
                annotators_by_id=annotators_by_id,
                actor=request.user,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(summary)


class AssignTaskView(APIView):
    """Manually assign or reassign a single task to an annotator. Managers only.

    Reassigning updates the existing task in place (no duplicate task is
    created). Passing a null/blank ``annotator_id`` unassigns the task. Non-null
    assignees must currently belong to a team granted to the task's project.
    """

    permission_classes = [IsManager]

    def post(self, request, pk):
        task = get_object_or_404(AnnotationTask, pk=pk)
        if not task.project.manager_reviewed:
            return Response(
                {"detail": "Review the project before assigning its tasks."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        annotator_id = request.data.get("annotator_id")

        if annotator_id in (None, "", "null"):
            try:
                task = assign_task_to_annotator(
                    task, annotator=None, actor=request.user
                )
            except ValueError as exc:
                return Response(
                    {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
                )
            return Response(AnnotationTaskSerializer(task, context={"request": request}).data)

        annotator = get_object_or_404(User, pk=annotator_id)
        if not is_annotator(annotator):
            return Response(
                {"detail": "Selected user is not an annotator."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not is_eligible_project_assignee(annotator, task.project):
            return Response(
                {
                    "detail": (
                        "Selected annotator is not a member of a team eligible "
                        "for this project."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            task = assign_task_to_annotator(
                task, annotator=annotator, actor=request.user
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(AnnotationTaskSerializer(task, context={"request": request}).data)


class MyTasksView(generics.ListAPIView):
    """Work still in the logged-in annotator's hands.

    Includes ``rejected`` alongside assigned/in-progress/revision-requested: a
    rejected task is not finished, it is work to redo, and the annotator can
    Annotate + Submit it again (nothing but ``annotation_locked`` closes a
    task — see ``services.can_submit_task``). Listing it under "completed"
    would hide the one thing they need to act on.
    """

    serializer_class = AnnotationTaskSerializer
    permission_classes = [IsAnnotator]

    def get_queryset(self):
        active = list(ACTIVE_TASK_STATUSES) + [
            TaskStatus.REVISION_REQUESTED,
            TaskStatus.REJECTED,
        ]
        return (
            AnnotationTask.objects.filter(
                assigned_to=self.request.user, status__in=active
            )
            .select_related("volume", "volume__dataset", "project")
        )


class MyCompletedTasksView(APIView):
    """Tasks the logged-in annotator has handed over: awaiting review, or
    approved. (Rejected ones live in :class:`MyTasksView` — see there.)"""

    permission_classes = [IsAnnotator]

    def get(self, request):
        done = [TaskStatus.SUBMITTED, TaskStatus.APPROVED]
        tasks = list(
            AnnotationTask.objects.filter(
                assigned_to=request.user, status__in=done
            )
            .select_related("volume", "volume__dataset", "project")
        )
        data = list(
            AnnotationTaskSerializer(
                tasks, many=True, context={"request": request}
            ).data
        )
        for item in data:
            item["history_key"] = f"task-{item['id']}"

        withdrawals = AssignmentWithdrawal.objects.filter(
            annotator=request.user
        ).select_related(
            "task", "task__volume", "task__volume__dataset", "task__project",
            "annotator",
        )
        for withdrawal in withdrawals:
            item = dict(
                AnnotationTaskSerializer(
                    withdrawal.task, context={"request": request}
                ).data
            )
            item.update({
                "history_key": f"withdrawal-{withdrawal.id}",
                "status": "cancelled",
                "assigned_to": withdrawal.annotator_id,
                "assigned_to_username": (
                    withdrawal.annotator.get_username()
                    if withdrawal.annotator else ""
                ),
                "can_submit": False,
                "can_annotate": False,
                "annotation_locked": False,
                "assignment_withdrawn": True,
                "withdrawal_reason": withdrawal.reason,
                "withdrawal_team": withdrawal.team_name,
                "withdrawn_at": withdrawal.withdrawn_at,
            })
            data.append(item)
        return Response(data)


class TaskVisualizationView(APIView):
    """Return viewer URL + state for a task's volume. Any role that can view."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "volume__dataset", "project"), pk=pk
        )
        if not can_view_task(request.user, task):
            return Response(
                {"detail": "You do not have access to this task."},
                status=status.HTTP_403_FORBIDDEN,
            )
        state = get_visualization_state(task)
        state["editable"] = can_annotate_task(request.user, task)
        return Response(state)


class SubmitTaskView(APIView):
    """An assigned annotator or manager uploads an offline label candidate.

    Re-submittable: each upload replaces only the previous pending *offline*
    submission. An online in-app checkpoint remains independently reviewable.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        task = get_object_or_404(AnnotationTask.objects.select_related("project"), pk=pk)
        if not can_submit_task(request.user, task):
            return Response(
                {"detail": _submit_denied_reason(request.user, task)},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = SubmitTaskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            submission = submit_annotation(
                task=task,
                annotator=request.user,
                label_file=serializer.validated_data["label_file"],
                notes=serializer.validated_data.get("notes", ""),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(
            AnnotationSubmissionSerializer(submission, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


def _annotate_denied_reason(user, task) -> str:
    """Why a paint/mutate endpoint refused — "locked" and "not yours" are
    different problems and the editor surfaces different next steps."""
    if task.annotation_locked:
        return (
            "This task was approved and closed for further annotation — "
            "it is view-only until a manager reopens it."
        )
    return "You do not have edit access to this task."


def _submit_denied_reason(user, task) -> str:
    """Why ``can_submit_task`` said no — the annotator deserves the actual
    reason, not a generic 403 (the two causes need different reactions)."""
    if task.annotation_locked:
        return (
            "This task was approved and closed for further annotation. "
            "Ask the manager to reopen it if you need to keep working."
        )
    return "You do not have edit access to this task."


class SubmitInappTaskView(APIView):
    """Submit a task's in-app working label copy for review — no file upload.

    Requires ``can_submit_task`` (manager or the assigned annotator, and the
    task not locked), same gating as the editor endpoints themselves — matches
    the intent of ``SubmitTaskView`` (annotator, or a manager acting for one)
    but keyed off edit access rather than assignment alone, since a manager who
    directly edited a task in-app should also be able to submit it.

    Submittable as many times as the annotator likes until a manager approves
    and locks: each call replaces only the previous pending online submission;
    an offline upload remains independently reviewable.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = get_object_or_404(
            AnnotationTask.objects.select_related(
                "project", "volume__project", "volume__dataset"
            ),
            pk=pk,
        )
        if not can_submit_task(request.user, task):
            return Response(
                {"detail": _submit_denied_reason(request.user, task)},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = SubmitInappTaskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            submission = submit_inapp_annotation(
                task=task,
                annotator=request.user,
                notes=serializer.validated_data.get("notes", ""),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(
            AnnotationSubmissionSerializer(submission, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class SubmissionListView(generics.ListAPIView):
    """List submissions. Managers see all; annotators see their own.

    **Latest pending row per task and source channel** — a task can therefore
    expose one Online and one Offline candidate together. Earlier same-channel
    rounds remain in durable task history but do not crowd the review queue.
    """

    serializer_class = AnnotationSubmissionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = AnnotationSubmission.objects.select_related(
            "task", "task__volume", "annotator"
        ).filter(id__in=latest_submission_ids())
        if not is_manager(self.request.user):
            qs = qs.filter(annotator=self.request.user)
        task_status = self.request.query_params.get("task_status")
        if task_status:
            qs = qs.filter(task__status=task_status)
        return qs


class SubmissionDetailView(generics.RetrieveAPIView):
    serializer_class = AnnotationSubmissionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = AnnotationSubmission.objects.select_related(
            "task", "task__volume", "annotator"
        )
        if not is_manager(self.request.user):
            qs = qs.filter(annotator=self.request.user)
        return qs


class ReviewSubmissionView(APIView):
    """Manager approves, rejects, or requests revision on a submission.

    ``allow_further_annotation`` (approve only) is the "annotator may keep
    working" switch: off (the default) locks the task, on leaves it open for
    another round. Reject/revision always reopen it.
    """

    permission_classes = [IsManager]

    def post(self, request, pk):
        submission = get_object_or_404(AnnotationSubmission, pk=pk)
        serializer = ReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            review = review_submission(
                submission=submission,
                reviewer=request.user,
                decision=serializer.validated_data["decision"],
                comments=serializer.validated_data.get("comments", ""),
                allow_further_annotation=serializer.validated_data.get(
                    "allow_further_annotation", False
                ),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        submission.refresh_from_db()
        return Response(
            {
                "review_id": review.id,
                "submission": AnnotationSubmissionSerializer(submission, context={"request": request}).data,
            }
        )


class TaskAnnotationLockView(APIView):
    """``POST /api/tasks/<pk>/annotation-lock/`` — body ``{"locked": bool}``.

    Lets a manager reopen a task they approved-and-closed (or close one they
    left open) without staging a fake review round. Managers only; the lock is
    what every paint/submit gate reads (``services.can_annotate_task`` /
    ``can_submit_task``).
    """

    permission_classes = [IsManager]

    def post(self, request, pk):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("project", "volume"), pk=pk
        )
        locked = request.data.get("locked")
        if not isinstance(locked, bool):
            return Response({"detail": "locked must be true or false."}, status=400)
        set_task_annotation_lock(task, locked=locked)
        return Response(
            AnnotationTaskSerializer(task, context={"request": request}).data
        )


# --- Slice streaming + in-app annotation -----------------------------------

from django.http import HttpResponse  # noqa: E402
from volumes.models import Volume  # noqa: E402

from .cellable_port.ai.registry import AiUnavailable  # noqa: E402
from .visualization.slice_io import (  # noqa: E402
    SliceIOError,
    render_image_slice_jpeg,
    render_image_slice_png,
    render_label_slice_png,
    render_region_mask_slice_png,
    volume_meta,
)


def _image_response(data: bytes, content_type: str, *, max_age: int) -> HttpResponse:
    resp = HttpResponse(data, content_type=content_type)
    resp["Cache-Control"] = f"private, max-age={max_age}"
    return resp


def _slice_params(request):
    axis = request.query_params.get("axis", "z")
    try:
        index = int(request.query_params.get("index", 0))
    except (TypeError, ValueError):
        index = 0
    window = request.query_params.get("window")
    level = request.query_params.get("level")
    window = float(window) if window not in (None, "") else None
    level = float(level) if level not in (None, "") else None
    return axis, index, window, level


class VolumeMetaView(APIView):
    """Shape/axes/dtype for a volume's image. Any role that can view it."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        volume = get_object_or_404(Volume.objects.select_related("project"), pk=pk)
        if not can_view_volume(request.user, volume):
            return Response({"detail": "No access to this volume."}, status=403)
        try:
            meta = volume_meta(volume.image_location)
        except (ValueError, SliceIOError) as exc:
            return Response({"detail": str(exc)}, status=400)
        meta["has_label"] = bool(volume.label_location)
        meta["has_region_mask"] = bool(volume.region_mask_location)
        meta["region_mask_coverage"] = volume.region_mask_coverage
        meta["volume_id"] = volume.id
        meta["ready_streaming"] = bool(volume.ready_streaming)
        # The ROI streams independently of the image: the editor mounts a chunk
        # source per layer and falls back per layer.
        meta["region_ready_streaming"] = bool(
            volume.region_mask_location and volume.region_ready_streaming
        )
        return Response(meta)


class VolumeSliceView(APIView):
    """Stream one image slice. Any role that can view.

    Default (no ``window``/``level``): JPEG, normalised against the volume's
    display range — small and fast to produce on CPU alone (libjpeg-turbo),
    which is what makes scrubbing through slices smooth on an HPC compute
    node with no GPU. Brightness/contrast are then adjusted client-side.
    Passing ``window``/``level`` explicitly still returns lossless PNG
    (back-compat for any caller that wants server-side windowing).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        volume = get_object_or_404(Volume.objects.select_related("project"), pk=pk)
        if not can_view_volume(request.user, volume):
            return Response({"detail": "No access to this volume."}, status=403)
        axis, index, window, level = _slice_params(request)
        try:
            if window is None and level is None:
                data = render_image_slice_jpeg(volume.image_location, axis, index)
                return _image_response(data, "image/jpeg", max_age=300)
            data = render_image_slice_png(
                volume.image_location, axis, index, window=window, level=level
            )
        except SliceIOError as exc:
            return Response({"detail": str(exc)}, status=400)
        return _image_response(data, "image/png", max_age=60)


class VolumeLabelSliceView(APIView):
    """Stream one label slice as an RGBA PNG overlay. Any role that can view."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        volume = get_object_or_404(Volume.objects.select_related("project"), pk=pk)
        if not can_view_volume(request.user, volume):
            return Response({"detail": "No access to this volume."}, status=403)
        if not volume.label_location:
            return Response({"detail": "Volume has no label."}, status=404)
        axis, index, _window, _level = _slice_params(request)
        # `region_only=1` is a *display* filter: instances touching the region
        # are rendered whole, the rest are dropped. It reads two planes and
        # writes nothing — the ROI write guards live in `region_mask.py`.
        region_only = request_roi_only(request.query_params.get("region_only"))
        try:
            png = render_label_slice_png(
                volume.label_location,
                axis,
                index,
                region_location=(
                    volume.region_mask_location
                    if region_only and volume.region_mask_location
                    else None
                ),
            )
        except SliceIOError as exc:
            return Response({"detail": str(exc)}, status=400)
        # Short cache: unlike the intensity image, labels change as people
        # annotate, and viewers watching progress should see recent edits.
        return _image_response(png, "image/png", max_age=15)


class VolumeRegionMaskSliceView(APIView):
    """Stream the immutable reference region; this endpoint has no write peer."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        volume = get_object_or_404(Volume.objects.select_related("project"), pk=pk)
        if not can_view_volume(request.user, volume):
            return Response({"detail": "No access to this volume."}, status=403)
        if not volume.region_mask_location:
            return Response({"detail": "Volume has no region mask."}, status=404)
        axis, index, _window, _level = _slice_params(request)
        try:
            data = render_region_mask_slice_png(
                volume.region_mask_location, axis, index
            )
        except SliceIOError as exc:
            return Response({"detail": str(exc)}, status=400)
        return _image_response(data, "image/png", max_age=300)


def _region_index_payload(volume, request):
    """``{axis, length, indices}`` — the planes of one axis that hold ROI.

    Read-only, and deliberately the whole list rather than "the nearest one to
    here": the viewer asks once per (volume, axis), then answers every later
    jump — and the "this plane already has region, do nothing" case — without
    another request. The alternative the client would otherwise be left with is
    one region PNG fetch per candidate plane.
    """
    from volumes.region_masks import region_nonempty_indices

    axis = request.query_params.get("axis", "z")
    indices = region_nonempty_indices(volume, axis)
    return {"axis": axis, "length": len(indices), "indices": indices}


class VolumeRegionIndexView(APIView):
    """Which slices of this volume contain any region. Any role that can view."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        volume = get_object_or_404(Volume.objects.select_related("project"), pk=pk)
        if not can_view_volume(request.user, volume):
            return Response({"detail": "No access to this volume."}, status=403)
        if not volume.region_mask_location:
            return Response({"detail": "Volume has no region mask."}, status=404)
        try:
            return Response(_region_index_payload(volume, request))
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)


def _decode_seeds(raw):
    """Decode ``[{z, rle:[[start,len]...], shape:[h,w]}]`` into ``{z: bool mask}``."""
    import numpy as np

    seeds = {}
    for item in raw or []:
        z = int(item["z"])
        h, w = int(item["shape"][0]), int(item["shape"][1])
        flat = np.zeros(h * w, dtype=bool)
        for start, length in item.get("rle", []):
            flat[int(start) : int(start) + int(length)] = True
        seeds[z] = flat.reshape(h, w)
    return seeds


def _decode_tracking_group(raw):
    """Turn one API queue/group record into explicit local subclass masks."""
    subclasses = {}
    for subclass in raw.get("subclasses", []):
        local_index = int(subclass.get("index", 0))
        if local_index < 1 or local_index in subclasses:
            raise ValueError("Subclass indices must be unique positive integers")
        subclasses[local_index] = _decode_seeds(subclass.get("seeds"))
    seed_zs = [z for seeds in subclasses.values() for z in seeds]
    return {
        "parent_id": int(raw.get("parent_id", 0)),
        "branch_seeds": subclasses,
        "z_range": [min(seed_zs), max(seed_zs)] if seed_zs else [0, 0],
    }


class TaskTrackView(APIView):
    """Plan fork-aware SAM2 tracking for one mito on a task. Editors only.

    Requesters are rejected here (mutation), matching the view-only UI. Body:
    ``{"seeds": [{z, rle, shape}], "z_range": [lo, hi]?}``.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        if not can_annotate_task(request.user, task):
            return Response(
                {"detail": _annotate_denied_reason(request.user, task)},
                status=status.HTTP_403_FORBIDDEN,
            )
        seeds = _decode_seeds(request.data.get("seeds"))
        if not seeds:
            return Response({"detail": "No seeds provided."}, status=400)
        z_range = request.data.get("z_range")
        if z_range:
            z_range = (int(z_range[0]), int(z_range[1]))
        try:
            group_id = request.data.get("parent_id")
            parent_id = int(group_id) if group_id is not None else get_label_max_id(task.volume) + 1
            result = plan_track_task_batch(
                task,
                [{
                    "parent_id": parent_id,
                    "branch_seeds": {1: seeds},
                    "z_range": z_range or [min(seeds), max(seeds)],
                }],
                axis=request.data.get("axis", "z"),
                pending_slices=request.data.get("pending_slices") or [],
            )
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(result)


class TaskTrackingPromptsView(APIView):
    """List/upsert/delete durable parent/subclass Track drafts."""

    permission_classes = [IsAuthenticated]

    def _task(self, request, pk, *, mutate=False):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        allowed = (
            can_annotate_task(request.user, task)
            if mutate
            else can_view_task(request.user, task)
        )
        if not allowed:
            return task, Response(
                {
                    "detail": (
                        _annotate_denied_reason(request.user, task)
                        if mutate
                        else "You do not have access to this task."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return task, None

    def get(self, request, pk):
        task, denied = self._task(request, pk)
        if denied:
            return denied
        return Response({
            "version": 1,
            "items": list_tracking_prompts(task),
            "pending_review": tracking_pending_review(task),
        })

    def post(self, request, pk):
        task, denied = self._task(request, pk, mutate=True)
        if denied:
            return denied
        try:
            prompts = replace_tracking_prompts(task, list(request.data.get("items", [])))
        except (TypeError, ValueError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response({"version": 1, "items": prompts, "pending_review": None})

    def put(self, request, pk):
        task, denied = self._task(request, pk, mutate=True)
        if denied:
            return denied
        try:
            prompt = upsert_tracking_prompt(task, request.data)
        except (TypeError, ValueError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(prompt)

    def delete(self, request, pk):
        task, denied = self._task(request, pk, mutate=True)
        if denied:
            return denied
        try:
            parent_id = int(
                request.data.get("parent_id")
                or request.query_params.get("parent_id", 0)
            )
        except (TypeError, ValueError):
            return Response({"detail": "Invalid parent_id"}, status=400)
        try:
            deleted = delete_tracking_prompt(task, parent_id)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response({"deleted": deleted})


class TaskTrackBatchView(APIView):
    """Propagate selected queued parents in one image/provider/save job."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        if not can_annotate_task(request.user, task):
            return Response(
                {"detail": _annotate_denied_reason(request.user, task)},
                status=status.HTTP_403_FORBIDDEN,
            )
        raw_groups = request.data.get("groups")
        if raw_groups is None:
            try:
                selected = {
                    int(v) for v in (request.data.get("parent_ids") or [])
                }
            except (TypeError, ValueError) as exc:
                return Response({"detail": str(exc)}, status=400)
            raw_groups = [
                prompt
                for prompt in list_tracking_prompts(task)
                if (
                    int(prompt.get("parent_id", 0)) in selected
                    if selected
                    else prompt.get("status") == "ready"
                )
            ]
        try:
            groups = [_decode_tracking_group(raw) for raw in raw_groups]
            result = plan_track_task_batch(
                task,
                groups,
                axis=request.data.get("axis", "z"),
                pending_slices=request.data.get("pending_slices") or [],
                overwrite_mode=request.data.get("overwrite_mode") or None,
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            SliceIOError,
            OSError,
        ) as exc:
            # Queue state records the failure; planning never touched labels.
            for raw in raw_groups or []:
                try:
                    failed = dict(raw)
                    failed["status"] = "error"
                    upsert_tracking_prompt(task, failed)
                except (TypeError, ValueError):
                    pass
            return Response({"detail": str(exc)}, status=400)
        return Response(result)


class TaskTrackReviewView(APIView):
    """Confirm or reject the currently pending Track batch preview."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        if not can_annotate_task(request.user, task):
            return Response(
                {"detail": _annotate_denied_reason(request.user, task)},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            result = review_tracking_preview(task, request.data.get("action", ""))
        except (TypeError, ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(result)


class TaskLabelStateView(APIView):
    """Editor bootstrap info: the next free instance id for this task's volume."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        task = get_object_or_404(AnnotationTask.objects.select_related("volume"), pk=pk)
        if not can_view_task(request.user, task):
            return Response(
                {"detail": "You do not have access to this task."}, status=403
            )
        try:
            max_id = get_label_max_id(task.volume)
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response({"max_label_id": max_id, "next_label_id": max_id + 1})


class TaskLabelIdsView(APIView):
    """Raw instance-id read/write for one label slice (the brush/eraser editor).

    GET returns the current ids RLE-encoded; PUT replaces the whole slice with
    client-painted ids and persists it. Editing requires ``can_edit_task``;
    viewing (so a manager/requester can watch progress) only needs view access.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        task = get_object_or_404(AnnotationTask.objects.select_related("volume"), pk=pk)
        if not can_view_task(request.user, task):
            return Response(
                {"detail": "You do not have access to this task."}, status=403
            )
        axis, index, _window, _level = _slice_params(request)
        try:
            return Response(get_label_slice_ids(task.volume, axis, index))
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)

    def put(self, request, pk):
        task = get_object_or_404(AnnotationTask.objects.select_related("volume"), pk=pk)
        if not can_annotate_task(request.user, task):
            return Response(
                {"detail": _annotate_denied_reason(request.user, task)}, status=403
            )
        axis = request.data.get("axis", "z")
        # "origin" tells the lifecycle tracker how to register a *brand new*
        # label id in this commit ("manual" — brush/erase/box-erase — or
        # "ai" — a committed Point/Box/Boundary preview); ids that already
        # have tracked state are always marked EDITED regardless. See
        # set_label_slice_ids's docstring.
        origin = request.data.get("origin", "manual")
        try:
            index = int(request.data.get("index"))
            shape = request.data["shape"]
            runs = request.data["runs"]
        except (TypeError, ValueError, KeyError):
            return Response({"detail": "axis, index, shape and runs are required."}, status=400)
        try:
            max_id = set_label_slice_ids(
                task.volume,
                axis,
                index,
                shape,
                runs,
                origin=origin,
                roi_only=request_roi_only(request.data.get("roi_only")),
            )
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response({"max_label_id": max_id, "next_label_id": max_id + 1})


# --- Cellable-ported interactive AI tools (Point/Box/Boundary, Seeds) -------
# See progress/history/19-cellable-parity-annotator-brief.md and
# annotation/cellable_port/ for what these port and why.

class TaskPredictMaskView(APIView):
    """Point Mask / Box Mask / Boundary preview — ``POST
    /api/tasks/<id>/predict-mask/``. Body: ``{"axis", "index", "mode":
    "points"|"box"|"boundary", "points"?, "point_labels"?, "box"?}``.

    Read-only: returns a candidate mask (label-RLE, 0/1) for the client to
    merge locally and commit through the existing label-ids PUT — this view
    never writes to the working label copy itself. Editors only, matching
    the other mutation-adjacent slice endpoints.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = get_object_or_404(AnnotationTask.objects.select_related("volume"), pk=pk)
        if not can_edit_task(request.user, task):
            return Response(
                {"detail": "You do not have edit access to this task."}, status=403
            )
        axis = request.data.get("axis", "z")
        mode = request.data.get("mode")
        try:
            index = int(request.data.get("index"))
        except (TypeError, ValueError):
            return Response({"detail": "axis and index are required."}, status=400)
        try:
            result = predict_ai_mask(
                task,
                axis,
                index,
                mode,
                points=request.data.get("points"),
                point_labels=request.data.get("point_labels"),
                box=request.data.get("box"),
                roi_only=request_roi_only(request.data.get("roi_only")),
            )
        except AiUnavailable as exc:
            return Response({"detail": str(exc)}, status=503)
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(result)


class TaskWarmEmbeddingView(APIView):
    """``POST /api/tasks/<id>/warm-embedding/`` — body ``{"axis", "index"}``.
    Pre-computes the EfficientSAM embedding for one slice so a subsequent
    Point/Box/Boundary predict on it is decoder-only. Fire-and-forget from
    the frontend (slice-open / AI-tool entry / neighbor prefetch — see
    ``progress/history/23-cellable-parity-ort-and-prompt-ux.md``); a missing
    model is reported as ``{"warmed": false}`` with 200, not a 503 — warming
    is an optimization, not something the UI should treat as an error."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = get_object_or_404(AnnotationTask.objects.select_related("volume"), pk=pk)
        if not can_edit_task(request.user, task):
            return Response(
                {"detail": "You do not have edit access to this task."}, status=403
            )
        axis = request.data.get("axis", "z")
        try:
            index = int(request.data.get("index"))
        except (TypeError, ValueError):
            return Response({"detail": "axis and index are required."}, status=400)
        try:
            warmed = warm_ai_embedding(
                task, axis, index, point=request.data.get("point")
            )
        except AiUnavailable:
            return Response({"warmed": False})
        except (ValueError, SliceIOError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response({"warmed": warmed})


class TaskWatershedView(APIView):
    """3D watershed (Seeds tool) — ``POST /api/tasks/<id>/watershed/``. Body:
    ``{"label": int, "seeds": [{"z", "y", "x"}, ...]}``. Editors only; writes
    a read-only plan for the client's pending/undo buffer."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        if not can_annotate_task(request.user, task):
            return Response(
                {"detail": _annotate_denied_reason(request.user, task)}, status=403
            )
        try:
            target_label = int(request.data.get("label"))
        except (TypeError, ValueError):
            return Response({"detail": "label is required."}, status=400)
        seeds_raw = request.data.get("seeds") or []
        try:
            seeds_zyx = [(int(s["z"]), int(s["y"]), int(s["x"])) for s in seeds_raw]
        except (KeyError, TypeError, ValueError):
            return Response({"detail": "seeds must be [{z, y, x}, ...]."}, status=400)
        if not seeds_zyx:
            return Response({"detail": "No seed points provided."}, status=400)
        try:
            result = plan_watershed_task(
                task,
                target_label,
                seeds_zyx,
                axis=request.data.get("axis", "z"),
                pending_slices=request.data.get("pending_slices") or [],
            )
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(result)


class TaskSplitComponentsView(APIView):
    """3D connected-component split (Split 3D tool) —
    ``POST /api/tasks/<id>/split-components/``. Body: ``{"label": int}``.
    Editors only; returns a read-only plan for pending application.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        if not can_annotate_task(request.user, task):
            return Response(
                {"detail": _annotate_denied_reason(request.user, task)}, status=403
            )
        try:
            target_label = int(request.data.get("label"))
        except (TypeError, ValueError):
            return Response({"detail": "label is required."}, status=400)
        try:
            result = plan_split_components_task(
                task,
                target_label,
                axis=request.data.get("axis", "z"),
                pending_slices=request.data.get("pending_slices") or [],
            )
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(result)



class TaskMergeLabelsView(APIView):
    """Merge two labels into the smaller id (Merge tool) —
    ``POST /api/tasks/<id>/merge-labels/``. Body: ``{"a": int, "b": int}``.
    The larger id is absorbed into the smaller. Editors only; read-only plan.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        if not can_annotate_task(request.user, task):
            return Response(
                {"detail": _annotate_denied_reason(request.user, task)}, status=403
            )
        # Reject the old directed {source, target} body so stale clients fail
        # loudly instead of merging the wrong way.
        if "source" in request.data or "target" in request.data:
            return Response(
                {
                    "detail": 'Use {"a": id, "b": id} — merge always keeps the smaller id.' 
                },
                status=400,
            )
        try:
            label_a = int(request.data.get("a"))
            label_b = int(request.data.get("b"))
        except (TypeError, ValueError):
            return Response(
                {"detail": "a and b label ids are required."}, status=400
            )
        try:
            result = plan_merge_labels_task(
                task,
                label_a,
                label_b,
                axis=request.data.get("axis", "z"),
                pending_slices=request.data.get("pending_slices") or [],
            )
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(result)


class TaskDeleteLabelPlanView(APIView):
    """Plan whole-volume Delete without changing pixels or lifecycle state."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        if not can_annotate_task(request.user, task):
            return Response(
                {"detail": _annotate_denied_reason(request.user, task)}, status=403
            )
        try:
            result = plan_delete_label_task(
                task,
                int(request.data.get("label")),
                axis=request.data.get("axis", "z"),
                pending_slices=request.data.get("pending_slices") or [],
            )
        except (TypeError, ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(result)



class TaskInterpolateView(APIView):
    """WEBKNOSSOS-style interpolation (ADR-006) — ``POST
    /api/tasks/<id>/interpolate/``. Body:
    ``{"axis", "first_index", "last_index", "label", "mode":
    "preview"|"apply", "overwrite_mode"?, "idempotency_key"?}``.

    ``preview`` (the default) plans and returns the intermediate 0/1 masks
    without writing anything; ``apply`` recomputes the same plan and commits
    it as exactly one undoable annotation operation. That split is ADR-006's
    "Interpolate -> preview -> confirm/cancel" — the client renders a preview
    it can still discard, and only a deliberate confirm mutates labels.

    Registered unconditionally and gated here rather than in the URL conf, so
    a disabled flag reads as "not enabled" (503) instead of "no such
    endpoint" (404) — see the note on the chunk-service routes in
    ``config/urls.py``. Editors only, like every other mutation-adjacent
    slice endpoint.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from .interpolation.core import InterpolationError
        from .interpolation.service import interpolation_enabled
        from .operations import OperationError
        from .visualization.slice_io import SliceIOError, decode_label_rle

        if not interpolation_enabled():
            return Response(
                {"detail": "Interpolation is disabled (FEATURE_INTERPOLATION)."},
                status=503,
            )
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        mode = (request.data.get("mode") or "preview").strip().lower()
        if mode not in ("preview", "apply"):
            return Response(
                {"detail": 'mode must be "preview" or "apply".'}, status=400
            )
        # Preview reads the working copy, which the editor may still be
        # painting into, so both modes require edit access — a viewer has no
        # business planning writes against someone else's staged labels.
        if not can_annotate_task(request.user, task):
            return Response(
                {"detail": _annotate_denied_reason(request.user, task)}, status=403
            )
        axis = request.data.get("axis", "z")
        try:
            first_index = int(request.data.get("first_index"))
            last_index = int(request.data.get("last_index"))
            label_id = int(request.data.get("label"))
        except (TypeError, ValueError):
            return Response(
                {"detail": "first_index, last_index and label are required."},
                status=400,
            )
        overwrite_mode = request.data.get("overwrite_mode") or None
        first_plane = None
        last_plane = None
        first_runs = request.data.get("first_runs")
        last_runs = request.data.get("last_runs")
        shape_raw = request.data.get("shape")
        if first_runs is not None or last_runs is not None:
            if first_runs is None or last_runs is None or not shape_raw:
                return Response(
                    {
                        "detail": "first_runs, last_runs and shape are required together "
                        "when supplying unsaved endpoint labels."
                    },
                    status=400,
                )
            try:
                shape = (int(shape_raw[0]), int(shape_raw[1]))
                first_plane = decode_label_rle(first_runs, shape)
                last_plane = decode_label_rle(last_runs, shape)
            except (TypeError, ValueError, IndexError, SliceIOError) as exc:
                return Response({"detail": str(exc)}, status=400)
        try:
            if mode == "preview":
                result = plan_task_interpolation(
                    task, axis=axis, first_index=first_index,
                    last_index=last_index, label_id=label_id,
                    overwrite_mode=overwrite_mode,
                    roi_only=request_roi_only(request.data.get("roi_only")),
                    first_labels=first_plane,
                    last_labels=last_plane,
                )
            else:
                result = apply_task_interpolation(
                    task, request.user, axis=axis, first_index=first_index,
                    last_index=last_index, label_id=label_id,
                    overwrite_mode=overwrite_mode,
                    idempotency_key=request.data.get("idempotency_key") or "",
                    roi_only=request_roi_only(request.data.get("roi_only")),
                )
        except InterpolationError as exc:
            # `reason` is machine-readable and already names what to change
            # (an empty endpoint, a depth over the cap, a locked task); pass
            # it through so the UI can act on it rather than only print it.
            code = {
                "locked": 409, "gone": 409, "idempotency_conflict": 409,
                "disabled": 503, "operations_disabled": 503,
            }.get(exc.reason, 400)
            return Response({"detail": str(exc), "reason": exc.reason}, status=code)
        except OperationError as exc:
            return Response({"detail": str(exc)}, status=409)
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(result)


class TaskFloodFillView(APIView):
    """Plan/apply classical 2-D or bounded z-depth flood fill.

    Body: ``axis``, ``index``, ``row``, ``col``, ``label``, optional ``depth``
    and ``overwrite_mode``, plus ``mode=preview|apply``. Apply always records
    one AnnotationOperation and is retry-safe with ``idempotency_key``.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from .operations import OperationError
        from .tools.common import ToolError
        from .tools.service import tools_enabled

        if not tools_enabled():
            return Response(
                {"detail": "Annotation tools are disabled (FEATURE_ANNOTATION_TOOLS)."},
                status=503,
            )
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        if not can_annotate_task(request.user, task):
            return Response(
                {"detail": _annotate_denied_reason(request.user, task)}, status=403
            )
        mode = str(request.data.get("mode") or "preview").lower()
        if mode not in ("preview", "apply"):
            return Response({"detail": 'mode must be "preview" or "apply".'}, status=400)
        try:
            params = {
                "axis": request.data.get("axis", "z"),
                "index": int(request.data.get("index")),
                "row": int(request.data.get("row")),
                "col": int(request.data.get("col")),
                "label_id": int(request.data.get("label")),
                "depth": int(request.data.get("depth") or 1),
                "overwrite_mode": request.data.get("overwrite_mode") or None,
                "roi_only": request_roi_only(request.data.get("roi_only")),
            }
        except (TypeError, ValueError):
            return Response(
                {"detail": "index, row, col and label are required integers."},
                status=400,
            )
        try:
            if mode == "preview":
                result = plan_task_flood_fill(task, **params)
            else:
                result = apply_task_flood_fill(
                    task, request.user, **params,
                    idempotency_key=request.data.get("idempotency_key") or "",
                )
        except ToolError as exc:
            code = 503 if exc.reason in ("disabled", "operations_disabled") else 409 if exc.reason in ("locked", "gone", "idempotency_conflict") else 400
            return Response({"detail": str(exc), "reason": exc.reason}, status=code)
        except OperationError as exc:
            return Response({"detail": str(exc)}, status=409)
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(result)


class TaskResetLabelsView(APIView):
    """``POST /api/tasks/<id>/labels/reset/`` — restore this task's *working*
    annotation to the volume's registered label mask.

    Destructive to the draft and nothing else: the registered source is read,
    never written (see ``reset_working_labels_to_registered``). Two callers, two
    permissions:

    * a **manager** may reset any task they can edit, from the Assign area — the
      point is handing an assignee a clean starting mask;
    * an **annotator** may reset their own annotatable task.

    Both land on ``can_annotate_task``, which is exactly "can edit and is not
    approved-and-locked". An approved task must not be silently un-annotated;
    the manager reopens it first.

    ``confirm`` must be true in the body. The UI asks before calling, and a
    second gate here is what stops a stray POST from discarding a day's work.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        if not can_annotate_task(request.user, task):
            return Response(
                {"detail": _annotate_denied_reason(request.user, task)},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not request.data.get("confirm"):
            return Response(
                {"detail": "Resetting working labels requires confirm=true."},
                status=400,
            )
        try:
            result = reset_working_labels_to_registered(task)
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(result)


class TaskRegionLabelIdsView(APIView):
    """``GET /api/tasks/<id>/region-label-ids/`` — the instance ids that touch
    this volume's ROI anywhere in z.

    "Region only" and "Hide non-ROI labels" are whole-instance decisions, so a
    per-plane overlap set is the wrong answer: a mito that enters the ROI on
    five of its forty planes must stay visible on all forty. Any role that can
    view the task; the response is read-only and touches no label pixels."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        task = get_object_or_404(AnnotationTask.objects.select_related("volume"), pk=pk)
        if not can_view_task(request.user, task):
            return Response(
                {"detail": "You do not have access to this task."}, status=403
            )
        try:
            return Response(get_region_label_ids(task.volume))
        except (ValueError, SliceIOError, OSError) as exc:
            # A shape mismatch or unreadable mask must not take Region only
            # down — the client falls back to what it can see for itself.
            return Response({"detail": str(exc)}, status=400)


class TaskLabelsSummaryView(APIView):
    """``GET /api/tasks/<id>/labels-summary/`` — per-label voxel count +
    first/last z across the whole working label volume. Backs the Labels
    panel's "All labels" scope. Any role that can view the task."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        task = get_object_or_404(AnnotationTask.objects.select_related("volume"), pk=pk)
        if not can_view_task(request.user, task):
            return Response(
                {"detail": "You do not have access to this task."}, status=403
            )
        try:
            return Response(get_labels_summary(task.volume))
        except (ValueError, SliceIOError, OSError) as exc:
            # A corrupt/unreadable working label must not take the Labels
            # panel down with a raw 500 — return a message the UI can show.
            return Response({"detail": str(exc)}, status=400)


# --- 3D Labels payloads (shared by the authed + public token endpoints) ----
# Both flavours of every 3D view differ only in how they resolve the volume
# and who may read it, so the id parsing and the binary packing live here
# once (progress/history/03-fix-hard-case-share-view.md: one mesh path for
# Annotate, View, and share — no share-only fork).


def _parse_label_ids(raw):
    """Normalise a request's label list (POST body list, or a legacy
    comma-separated GET param) to ``list[int]``. Returns ``None`` if it isn't
    a well-formed id list — the caller turns that into a 400."""
    if isinstance(raw, str):
        raw = [v for v in raw.split(",") if v.strip() != ""]
    if not isinstance(raw, list):
        return None
    try:
        return [int(v) for v in raw]
    except (TypeError, ValueError):
        return None


def _labels_3d_grid_response(volume, label_ids, *, public: bool):
    """Legacy voxel-grid body: little-endian ``uint32 dz, dy, dx, num_labels``
    then ``num_labels`` x (``int32 label_id`` + ``dz*dy*dx`` 0/1 bytes)."""
    import struct

    preview = get_labels_3d_preview(volume, label_ids, readonly=public)
    dz, dy, dx = preview["shape"]
    grids = preview["grids"]
    body = bytearray(struct.pack("<IIII", dz, dy, dx, len(grids)))
    for lid, grid in grids.items():
        body += struct.pack("<i", lid)
        body += grid.tobytes()
    resp = HttpResponse(bytes(body), content_type="application/octet-stream")
    resp["Cache-Control"] = f"{'public' if public else 'private'}, max-age=10"
    return resp


def _labels_3d_mesh_response(volume, label_ids, *, public: bool):
    """Iso-surface mesh body (little-endian, every field 4-byte aligned so the
    client can view it as typed arrays with no copying)::

        uint32  version = 1
        uint32  num_meshes
        uint32  truncated          # labels dropped by the triangle budget
        uint32  reserved = 0
        float32 origin_z, origin_y, origin_x   # voxel coords
        float32 size_z, size_y, size_x
        float32 voxel_z, voxel_y, voxel_x      # physical voxel size (z-scale)
        per mesh:
            int32   label_id
            uint32  num_vertices
            uint32  num_triangles
            float32 vertices[num_vertices * 3]   # z, y, x
            uint32  indices[num_triangles * 3]
    """
    import struct

    result = get_labels_3d_mesh(volume, label_ids, readonly=public)
    meshes = result["meshes"]
    body = bytearray(struct.pack("<IIII", 1, len(meshes), int(result["truncated"]), 0))
    body += struct.pack("<fff", *(float(v) for v in result["origin"]))
    body += struct.pack("<fff", *(float(v) for v in result["size"]))
    body += struct.pack("<fff", *(float(v) for v in result["voxel_size"]))
    for mesh in meshes:
        verts = mesh["vertices"]
        faces = mesh["faces"]
        body += struct.pack("<iII", int(mesh["id"]), len(verts), len(faces))
        body += verts.astype("<f4", copy=False).tobytes()
        body += faces.astype("<u4", copy=False).tobytes()
    resp = HttpResponse(bytes(body), content_type="application/octet-stream")
    resp["Cache-Control"] = f"{'public' if public else 'private'}, max-age=10"
    return resp


class TaskLabels3DView(APIView):
    """``POST /api/tasks/<id>/labels-3d/`` with body ``{"labels": [1,2,3]}``
    (preferred) or legacy ``GET …?labels=1,2,3`` — the compact binary voxel
    grid for the requested label ids (see ``cellable_port/labels_3d.py``).

    **Legacy**: the 3D Labels panel renders ``labels-3d-mesh/`` (real
    iso-surfaces) now; this stays for older clients and as a fallback that
    needs no scikit-image.

    Bulk actions like "3D slice" / "3D all" can request hundreds of ids; those
    must go in the POST body. Putting them in the query string blows past
    proxy/header size limits and surfaces as HTTP 431 / "Preview failed".
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        return self._respond(request, pk, request.query_params.get("labels", ""))

    def post(self, request, pk):
        return self._respond(request, pk, request.data.get("labels", []))

    def _respond(self, request, pk, raw):
        label_ids = _parse_label_ids(raw)
        if label_ids is None:
            return Response({"detail": "labels must be a list of integer ids."}, status=400)
        task = get_object_or_404(AnnotationTask.objects.select_related("volume"), pk=pk)
        if not can_view_task(request.user, task):
            return Response(
                {"detail": "You do not have access to this task."}, status=403
            )
        try:
            return _labels_3d_grid_response(task.volume, label_ids, public=False)
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)


class TaskLabels3DMeshView(APIView):
    """``POST /api/tasks/<id>/labels-3d-mesh/`` with body ``{"labels": [...]}``
    — marching-cubes iso-surfaces for those labels, the payload the 3D Labels
    panel actually renders. See :func:`_labels_3d_mesh_response` for the wire
    format and ``cellable_port/labels_3d.py`` for the geometry."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        label_ids = _parse_label_ids(request.data.get("labels", []))
        if label_ids is None:
            return Response({"detail": "labels must be a list of integer ids."}, status=400)
        task = get_object_or_404(AnnotationTask.objects.select_related("volume"), pk=pk)
        if not can_view_task(request.user, task):
            return Response(
                {"detail": "You do not have access to this task."}, status=403
            )
        try:
            return _labels_3d_mesh_response(task.volume, label_ids, public=False)
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)


class TaskLabelLifecycleView(APIView):
    """``POST /api/tasks/<id>/labels/<label_id>/lifecycle/`` — Cellable-parity
    label lifecycle actions (Filters Options' Verify/Revert/Reject), body
    ``{"action": "verify"|"unverify"|"revert"|"reject"}``. Editors only —
    these mutate the working copy (revert/reject) or its metadata sidecar
    (all four). Destructive actions (revert/reject) get their confirm()
    dialog in the frontend, per `04-incident-data-safety.md`."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk, label_id):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        if not can_annotate_task(request.user, task):
            return Response(
                {"detail": _annotate_denied_reason(request.user, task)}, status=403
            )
        action = request.data.get("action")
        try:
            result = set_label_lifecycle_action(task.volume, label_id, action)
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(result)


# --- Hard cases: project-scoped (authed) + public token link ---------------
# See progress/history/{02-share-hard-case,05-submit-people-hardcases}.md.
# Recording a case is auth-gated to someone who can open Annotate; reading one
# needs either project membership (the authed views below) or the unguessable
# token (AllowAny). The public views are *read-only clones* of the authed
# viewer endpoints, scoped to the case's task/volume — no write path ever
# accepts a token.

from rest_framework.permissions import AllowAny  # noqa: E402


class HardCaseCreateView(APIView):
    """``POST /api/tasks/<pk>/hard-cases/`` — record the Active label as a
    hard case for this task's project. Body: ``{"label_id": <activeId>}``.
    Editors only (manager or the assigned annotator — anyone who can open
    Annotate); a locked task can still have cases recorded, since flagging a
    hard case is not annotating it.

    Returns the full case row (``HardCaseSerializer``), which includes both
    the in-app ``app_url`` and the public ``url``/``token`` for the optional
    copyable link.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        if not can_edit_task(request.user, task):
            return Response(
                {"detail": "You do not have access to record a hard case here."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            label_id = int(request.data.get("label_id"))
        except (TypeError, ValueError):
            return Response({"detail": "label_id is required."}, status=400)
        if label_id <= 0:
            return Response({"detail": "label_id must be a positive instance id."}, status=400)
        # Only record labels that actually exist in the volume mask.
        summary = get_labels_summary(task.volume)
        known = {int(row["id"]) for row in summary.get("labels", [])}
        if label_id not in known:
            return Response(
                {
                    "detail": (
                        f"Label {label_id} does not exist on this volume — "
                        "pick an Active id that has painted voxels."
                    )
                },
                status=400,
            )
        case = create_hard_case(task=task, user=request.user, label_id=label_id)
        return Response(
            HardCaseSerializer(case, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class HardCaseListView(generics.ListAPIView):
    """``GET /api/hard-cases/[?project=<id>][&volume=<id>][&status=open]`` —
    the Hard Cases inbox, newest first (the model's own ordering).

    Scoped to what the caller may see (project membership — see
    ``services.visible_hard_cases``); the optional filters back the per-project
    and per-volume sections without a second endpoint.
    """

    serializer_class = HardCaseSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        params = self.request.query_params
        filters = {}
        for name in ("project", "volume"):
            raw = params.get(name)
            if raw in (None, ""):
                continue
            try:
                filters[name] = int(raw)
            except (TypeError, ValueError):
                # A junk id means "no such project/volume" — an empty list, not
                # a 500 from Django coercing it, and not a silently *unfiltered*
                # list either (which would show cases the caller didn't ask for).
                return HardCase.objects.none()

        qs = visible_hard_cases(self.request.user, **filters)
        case_status = params.get("status")
        if case_status:
            qs = qs.filter(status=case_status)
        return qs


class HardCaseDetailView(APIView):
    """``GET /api/hard-cases/<pk>/`` — one case for a project member.

    Adds what the viewer needs to mount the shared ``AnnotationCanvas`` on the
    case's task (``z_start``/``z_end``/``volume``), so the case page reuses the
    ordinary authed viewer endpoints rather than a case-only read path.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        case = get_object_or_404(
            HardCase.objects.select_related(
                "task", "volume", "project", "created_by", "resolved_by"
            ),
            pk=pk,
        )
        if not can_view_hard_case(request.user, case):
            return Response(
                {"detail": "You do not have access to this hard case."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(HardCaseSerializer(case, context={"request": request}).data)


class HardCaseStatusView(APIView):
    """``POST /api/hard-cases/<pk>/status/`` — body ``{"status": "resolved" |
    "open"}``. "Take down" is creator-or-manager only and *resolves* rather
    than deletes: everyone else keeps read access to settled cases."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        case = get_object_or_404(
            HardCase.objects.select_related("task", "project", "created_by"), pk=pk
        )
        if not can_take_down_hard_case(request.user, case):
            return Response(
                {"detail": "Only the person who recorded this case, or a manager, can take it down."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            set_hard_case_status(
                case, status=request.data.get("status"), user=request.user
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(HardCaseSerializer(case, context={"request": request}).data)


class HardCaseRevokeView(APIView):
    """``POST /api/hard-cases/<pk>/revoke/`` — body ``{"revoked": bool}``.
    Kills (or restores) the **public token link** only; project members keep
    their in-app access either way."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        case = get_object_or_404(
            HardCase.objects.select_related("task", "project", "created_by"), pk=pk
        )
        if not can_take_down_hard_case(request.user, case):
            return Response(
                {"detail": "Only the person who recorded this case, or a manager, can revoke its link."},
                status=status.HTTP_403_FORBIDDEN,
            )
        revoked = request.data.get("revoked")
        if not isinstance(revoked, bool):
            return Response({"detail": "revoked must be true or false."}, status=400)
        set_hard_case_revoked(case, revoked=revoked)
        return Response(HardCaseSerializer(case, context={"request": request}).data)


class _PublicHardCaseView(APIView):
    """Base for the public (token-gated) read endpoints. AllowAny + no
    authentication (a stale token in a viewer's localStorage must never turn
    a public page into a 401), resolves the case by token or 404s."""

    permission_classes = [AllowAny]
    authentication_classes = []  # ignore any Authorization header entirely

    def get_case_or_404(self, token):
        return get_public_hard_case(token)


class TaskPublicShareView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from .task_sharing import create_token

        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        if not can_view_task(request.user, task):
            return Response({"detail": "You cannot view this task."}, status=403)
        token = create_token(task)
        return Response({"token": token, "url": f"/share/task/{token}"})


class _PublicTaskShareMixin:
    def get_case_or_404(self, token):
        from types import SimpleNamespace
        from .task_sharing import resolve_token

        task = resolve_token(token)
        return (
            SimpleNamespace(task=task, task_id=task.id, label_id=None)
            if task is not None
            else None
        )


class PublicHardCaseMetaView(_PublicHardCaseView):
    """``GET /api/public/hard-cases/<token>/meta/`` → everything the shared
    viewer needs to mount: task/volume identity, the shared ``label_id``, the
    task's z-range, and the volume's shape/dtype/axes/display_range/has_label
    (a superset of the authed ``VolumeMetaView`` payload)."""

    def get(self, request, token):
        case = self.get_case_or_404(token)
        if case is None:
            return Response({"detail": "Hard case not found."}, status=404)
        volume = case.task.volume
        try:
            meta = volume_meta(volume.image_location)
        except SliceIOError as exc:
            return Response({"detail": str(exc)}, status=400)
        meta["has_label"] = bool(volume.label_location)
        meta["has_region_mask"] = bool(volume.region_mask_location)
        meta["region_mask_coverage"] = volume.region_mask_coverage
        meta["volume_id"] = volume.id
        # Share readers stream from the same pyramids the authenticated viewer
        # uses, through the share-scoped token endpoints below — they still
        # never mint a *user* chunk credential. Reporting the real flags is what
        # lets the shared canvas mount a chunk source at all; with them pinned
        # to False it silently fell back to whole-plane PNGs even for volumes
        # whose derivatives were built and ready.
        meta["ready_streaming"] = bool(volume.ready_streaming)
        meta["region_ready_streaming"] = bool(
            volume.region_mask_location and volume.region_ready_streaming
        )
        meta["task_id"] = case.task_id
        meta["label_id"] = case.label_id
        meta["z_start"] = case.task.z_start
        meta["z_end"] = case.task.z_end
        meta["volume_name"] = volume.name
        meta["project_title"] = case.task.project.title
        return Response(meta)


class PublicHardCaseSliceView(_PublicHardCaseView):
    """``GET /api/public/hard-cases/<token>/slice/?axis=&index=`` → one JPEG
    image slice (same encoding/normalisation as the authed viewer)."""

    def get(self, request, token):
        case = self.get_case_or_404(token)
        if case is None:
            return Response({"detail": "Hard case not found."}, status=404)
        axis, index, _window, _level = _slice_params(request)
        try:
            data = render_image_slice_jpeg(case.task.volume.image_location, axis, index)
        except SliceIOError as exc:
            return Response({"detail": str(exc)}, status=400)
        # Public + immutable-per-(axis,index): safe to cache in the browser.
        resp = HttpResponse(data, content_type="image/jpeg")
        resp["Cache-Control"] = "public, max-age=300"
        return resp


class PublicHardCaseRegionMaskSliceView(_PublicHardCaseView):
    def get(self, request, token):
        case = self.get_case_or_404(token)
        if case is None:
            return Response({"detail": "Share not found."}, status=404)
        location = case.task.volume.region_mask_location
        if not location:
            return Response({"detail": "Volume has no region mask."}, status=404)
        axis, index, _window, _level = _slice_params(request)
        try:
            data = render_region_mask_slice_png(location, axis, index)
        except SliceIOError as exc:
            return Response({"detail": str(exc)}, status=400)
        response = HttpResponse(data, content_type="image/png")
        response["Cache-Control"] = "public, max-age=300"
        return response


class PublicHardCaseRegionIndexView(_PublicHardCaseView):
    """``GET /api/public/hard-cases/<token>/region-index/?axis=`` → the planes
    of that axis holding any region, so a share can jump to them too."""

    def get(self, request, token):
        case = self.get_case_or_404(token)
        if case is None:
            return Response({"detail": "Share not found."}, status=404)
        volume = case.task.volume
        if not volume.region_mask_location:
            return Response({"detail": "Volume has no region mask."}, status=404)
        try:
            return Response(_region_index_payload(volume, request))
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)


class PublicHardCaseLabelStateView(_PublicHardCaseView):
    """``GET /api/public/hard-cases/<token>/label-state/`` → the working
    copy's max/next instance id (read-only)."""

    def get(self, request, token):
        case = self.get_case_or_404(token)
        if case is None:
            return Response({"detail": "Hard case not found."}, status=404)
        try:
            max_id = get_label_max_id_readonly(case.task.volume)
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response({"max_label_id": max_id, "next_label_id": max_id + 1})


class PublicHardCaseLabelIdsView(_PublicHardCaseView):
    """``GET /api/public/hard-cases/<token>/label-ids/?axis=&index=`` → the
    raw instance ids of one label slice, RLE-encoded (read-only; there is no
    PUT here — the shared viewer never writes)."""

    def get(self, request, token):
        case = self.get_case_or_404(token)
        if case is None:
            return Response({"detail": "Hard case not found."}, status=404)
        axis, index, _window, _level = _slice_params(request)
        try:
            return Response(
                get_label_slice_ids_readonly(case.task.volume, axis, index)
            )
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)


class PublicHardCaseLabelsSummaryView(_PublicHardCaseView):
    """``GET /api/public/hard-cases/<token>/labels-summary/`` → the whole-
    volume per-label summary (so a recipient can browse/reveal other labels)."""

    def get(self, request, token):
        case = self.get_case_or_404(token)
        if case is None:
            return Response({"detail": "Hard case not found."}, status=404)
        try:
            return Response(get_labels_summary(case.task.volume, readonly=True))
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)


class PublicHardCaseRegionLabelIdsView(_PublicHardCaseView):
    """``GET /api/public/hard-cases/<token>/region-label-ids/`` → the same
    volume-wide ROI membership the authed viewer uses, so Region only means the
    same thing on a shared link as it does in Annotate."""

    def get(self, request, token):
        case = self.get_case_or_404(token)
        if case is None:
            return Response({"detail": "Hard case not found."}, status=404)
        try:
            return Response(get_region_label_ids(case.task.volume, readonly=True))
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)


class PublicHardCaseLabels3DView(_PublicHardCaseView):
    """``POST /api/public/hard-cases/<token>/labels-3d/`` (body
    ``{"labels": [...]}``) → the same legacy voxel grid the authed
    ``TaskLabels3DView`` returns, scoped to the share's volume."""

    def post(self, request, token):
        case = self.get_case_or_404(token)
        if case is None:
            return Response({"detail": "Hard case not found."}, status=404)
        label_ids = _parse_label_ids(request.data.get("labels", []))
        if label_ids is None:
            return Response({"detail": "labels must be a list of integer ids."}, status=400)
        try:
            return _labels_3d_grid_response(case.task.volume, label_ids, public=True)
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)


class PublicHardCaseLabels3DMeshView(_PublicHardCaseView):
    """``POST /api/public/hard-cases/<token>/labels-3d-mesh/`` — the same
    iso-surface meshes ``TaskLabels3DMeshView`` returns, so the shared viewer
    renders real 3D through exactly the same backend path (no share-only
    geometry code)."""

    def post(self, request, token):
        case = self.get_case_or_404(token)
        if case is None:
            return Response({"detail": "Hard case not found."}, status=404)
        label_ids = _parse_label_ids(request.data.get("labels", []))
        if label_ids is None:
            return Response({"detail": "labels must be a list of integer ids."}, status=400)
        try:
            return _labels_3d_mesh_response(case.task.volume, label_ids, public=True)
        except (ValueError, SliceIOError, OSError) as exc:
            return Response({"detail": str(exc)}, status=400)


class PublicHardCaseChunkCapabilitiesView(_PublicHardCaseView):
    """``GET <share base>/chunks/capabilities/?layer=`` — the pyramid grid for a
    volume this share already authorizes.

    Same payload as the authenticated ``VolumeChunkCapabilitiesView``: the
    share's own token gate replaces the user ACL, and nothing else about the
    description changes (see ``service.capabilities_for_volume``).
    """

    def get(self, request, token):
        from volumes.chunks import service as chunk_service

        case = self.get_case_or_404(token)
        if case is None:
            return Response({"detail": "Share not found."}, status=404)
        try:
            return Response(
                chunk_service.capabilities_for_volume(
                    volume=case.task.volume,
                    layer=str(request.GET.get("layer") or "image"),
                )
            )
        except chunk_service.ChunkServiceError as exc:
            return Response({"detail": str(exc), "reason": exc.reason}, status=exc.status)


class PublicHardCaseChunkTokenView(_PublicHardCaseView):
    """``POST <share base>/chunks/token/`` — a short-lived, volume-scoped chunk
    token for an anonymous share recipient.

    Why this is not a widening of the share: the recipient can already read
    every voxel of this volume through the share's ``slice`` endpoint. This
    hands back the *same* pixels over the streaming transport, and the token it
    mints names exactly one volume and the layers requested, so it cannot be
    replayed against anything else in (or outside) the share. No user identity
    is embedded — see ``service.SHARE_ISSUER_ID`` — so nothing downstream can
    resolve it to an account, and the TTL is deliberately shorter than the
    authenticated default because a share is revoked far more readily than an
    account is disabled (``service.SHARE_TOKEN_TTL_SECONDS``).

    The signed read route itself (``/api/chunks/signed/…``) is unchanged: it was
    already anonymous and signature-verified. Only *minting* ever required an
    account, which is exactly the step this replaces with the share's own gate.
    """

    def post(self, request, token):
        from volumes.chunks import service as chunk_service
        from volumes.chunks.tokens import ChunkScope, TokenError

        case = self.get_case_or_404(token)
        if case is None:
            return Response({"detail": "Share not found."}, status=404)
        raw_scope = request.data.get("scope")
        try:
            scope = ChunkScope.from_list(raw_scope) if raw_scope else None
        except TokenError as exc:
            return Response({"detail": str(exc), "reason": exc.reason}, status=400)
        try:
            return Response(
                chunk_service.issue_token_for_shared_volume(
                    volume=case.task.volume,
                    mags=request.data.get("mags"),
                    layers=request.data.get("layers"),
                    scope=scope,
                )
            )
        except chunk_service.ChunkServiceError as exc:
            return Response({"detail": str(exc), "reason": exc.reason}, status=exc.status)


class PublicTaskShareMetaView(_PublicTaskShareMixin, PublicHardCaseMetaView):
    pass


class PublicTaskShareSliceView(_PublicTaskShareMixin, PublicHardCaseSliceView):
    pass


class PublicTaskShareRegionMaskSliceView(
    _PublicTaskShareMixin, PublicHardCaseRegionMaskSliceView
):
    pass


class PublicTaskShareRegionIndexView(
    _PublicTaskShareMixin, PublicHardCaseRegionIndexView
):
    pass


class PublicTaskShareLabelStateView(
    _PublicTaskShareMixin, PublicHardCaseLabelStateView
):
    pass


class PublicTaskShareLabelIdsView(
    _PublicTaskShareMixin, PublicHardCaseLabelIdsView
):
    pass


class PublicTaskShareLabelsSummaryView(
    _PublicTaskShareMixin, PublicHardCaseLabelsSummaryView
):
    pass


class PublicTaskShareRegionLabelIdsView(
    _PublicTaskShareMixin, PublicHardCaseRegionLabelIdsView
):
    pass


class PublicTaskShareLabels3DView(
    _PublicTaskShareMixin, PublicHardCaseLabels3DView
):
    pass


class PublicTaskShareLabels3DMeshView(
    _PublicTaskShareMixin, PublicHardCaseLabels3DMeshView
):
    pass


class PublicTaskShareChunkCapabilitiesView(
    _PublicTaskShareMixin, PublicHardCaseChunkCapabilitiesView
):
    pass


class PublicTaskShareChunkTokenView(
    _PublicTaskShareMixin, PublicHardCaseChunkTokenView
):
    pass


class _PublicHierarchyShareMixin:
    """Adapt a scoped public share + selected volume to the read-only viewer."""

    def dispatch(self, request, *args, **kwargs):
        self.share_volume_id = kwargs.pop("volume_id", None)
        return super().dispatch(request, *args, **kwargs)

    def get_case_or_404(self, token):
        from types import SimpleNamespace
        from projects.models import PublicShare
        from rest_framework.exceptions import APIException

        share = PublicShare.objects.select_related("project", "dataset", "volume").filter(token=token).first()
        if share is None:
            return None
        if share.revoked_at:
            exc = APIException("The manager closed this share.")
            exc.status_code = 410
            raise exc
        volume = share.project.volumes.filter(pk=self.share_volume_id).first()
        if volume is None or (share.scope == "dataset" and volume.dataset_id != share.dataset_id) or (share.scope == "volume" and volume.id != share.volume_id):
            return None
        task = volume.tasks.select_related("project").first()
        if task is None:
            task = SimpleNamespace(
                id=0, pk=0, volume=volume, project=share.project,
                z_start=0, z_end=max((volume.shape_z or 1) - 1, 0),
            )
        return SimpleNamespace(task=task, task_id=task.id, label_id=None)


class PublicHierarchyShareMetaView(_PublicHierarchyShareMixin, PublicHardCaseMetaView): pass
class PublicHierarchyShareSliceView(_PublicHierarchyShareMixin, PublicHardCaseSliceView): pass
class PublicHierarchyShareRegionMaskSliceView(_PublicHierarchyShareMixin, PublicHardCaseRegionMaskSliceView): pass
class PublicHierarchyShareRegionIndexView(_PublicHierarchyShareMixin, PublicHardCaseRegionIndexView): pass
class PublicHierarchyShareLabelStateView(_PublicHierarchyShareMixin, PublicHardCaseLabelStateView): pass
class PublicHierarchyShareLabelIdsView(_PublicHierarchyShareMixin, PublicHardCaseLabelIdsView): pass
class PublicHierarchyShareLabelsSummaryView(_PublicHierarchyShareMixin, PublicHardCaseLabelsSummaryView): pass
class PublicHierarchyShareRegionLabelIdsView(_PublicHierarchyShareMixin, PublicHardCaseRegionLabelIdsView): pass
class PublicHierarchyShareLabels3DView(_PublicHierarchyShareMixin, PublicHardCaseLabels3DView): pass
class PublicHierarchyShareLabels3DMeshView(_PublicHierarchyShareMixin, PublicHardCaseLabels3DMeshView): pass
class PublicHierarchyShareChunkCapabilitiesView(_PublicHierarchyShareMixin, PublicHardCaseChunkCapabilitiesView): pass
class PublicHierarchyShareChunkTokenView(_PublicHierarchyShareMixin, PublicHardCaseChunkTokenView): pass
