"""HTTP surface for quality scores and gold-standard configuration.

Read-only except for the gold-standard switch, which is manager-only: marking
a volume as a known test is a measurement decision, not annotation work.

Scores themselves are never created over HTTP. They are written by
``annotation.quality_scoring`` at the moment a submission is made or approved,
so a number always has a recorded provenance and cannot be posted by a client.

Returns 503 when ``FEATURE_QUALITY_METRICS`` is off.
"""

from __future__ import annotations

from django.db.models import Avg, Count
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.roles import is_manager
from core.choices import QualityScoreKind, SubmissionReviewStatus
from projects.models import Project
from volumes.models import Volume

from .models import AnnotationSubmission, QualityScore
from .quality_scoring import annotator_quality, quality_metrics_enabled

_DISABLED = Response(
    {"detail": "Quality metrics are not enabled.", "reason": "disabled"},
    status=status.HTTP_503_SERVICE_UNAVAILABLE,
)


def serialize(score) -> dict:
    """One score. Every metric may legitimately be ``None`` — see the model."""
    return {
        "id": score.pk,
        "kind": score.kind,
        "reference_submission": score.reference_submission_id,
        "dice": score.dice,
        "iou": score.iou,
        "precision": score.precision,
        "recall": score.recall,
        "instance_f1": score.instance_f1,
        "false_merges": score.false_merges,
        "false_splits": score.false_splits,
        "variation_of_information": score.variation_of_information,
        "provider": score.provider,
        "detail": score.detail or {},
        "computed_at": score.computed_at.isoformat(),
    }


class SubmissionQualityView(APIView):
    """``GET /api/submissions/<pk>/quality/`` — every score for one submission."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if not quality_metrics_enabled():
            return _DISABLED
        submission = get_object_or_404(
            AnnotationSubmission.objects.select_related("task__project"), pk=pk
        )
        from .services import can_view_task

        if not can_view_task(request.user, submission.task):
            return Response(
                {"detail": "You do not have access to this submission."},
                status=status.HTTP_403_FORBIDDEN,
            )
        scores = submission.quality_scores.all()
        return Response({"results": [serialize(score) for score in scores]})


class ProjectQualityView(APIView):
    """``GET /api/projects/<pk>/quality/`` — the project's quality standing."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if not quality_metrics_enabled():
            return _DISABLED
        project = get_object_or_404(Project, pk=pk)
        from .services import is_project_member

        if not is_project_member(request.user, project):
            return Response(
                {"detail": "You do not have access to this project."},
                status=status.HTTP_403_FORBIDDEN,
            )

        scores = QualityScore.objects.filter(submission__task__project=project)

        # One grouped query per kind, plus one grouped query per annotator.
        # ``Avg`` already ignores NULLs, so an unmeasured metric never drags a
        # mean toward zero.
        by_kind = {
            row["kind"]: {
                "count": row["n"],
                "mean_dice": row["mean_dice"],
                "mean_instance_f1": row["mean_f1"],
            }
            for row in scores.values("kind").annotate(
                n=Count("id"),
                mean_dice=Avg("dice"),
                mean_f1=Avg("instance_f1"),
            )
        }
        for kind in QualityScoreKind:
            by_kind.setdefault(
                kind.value,
                {"count": 0, "mean_dice": None, "mean_instance_f1": None},
            )

        people = [
            {
                "user_id": row["submission__annotator"],
                "username": row["submission__annotator__username"],
                "scored": row["n"],
                "mean_dice": row["mean_dice"],
                "mean_instance_f1": row["mean_f1"],
                "false_merges": row["merges"],
                "false_splits": row["splits"],
            }
            for row in scores.filter(submission__annotator__isnull=False)
            .values("submission__annotator", "submission__annotator__username")
            .annotate(
                n=Count("id"),
                mean_dice=Avg("dice"),
                mean_f1=Avg("instance_f1"),
                merges=Avg("false_merges"),
                splits=Avg("false_splits"),
            )
            .order_by("submission__annotator__username")
        ]

        gold_volumes = Volume.objects.filter(
            project=project, is_gold_standard=True
        ).values("id", "name", "reference_submission_id")

        return Response(
            {
                "by_kind": by_kind,
                "people": people,
                "gold_standard_volumes": list(gold_volumes),
            }
        )


class VolumeApprovedSubmissionsView(APIView):
    """``GET /api/volumes/<pk>/approved-submissions/`` — reference candidates.

    Purpose-built rather than reusing ``SubmissionListView``: that one returns
    the *latest pending* row per task and channel, which is the review queue —
    the exact opposite of what a gold-standard reference needs. A reference has
    to be something already approved, and any approved round qualifies, not
    only the most recent.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if not quality_metrics_enabled():
            return _DISABLED
        if not is_manager(request.user):
            return Response(
                {"detail": "Manager access required."},
                status=status.HTTP_403_FORBIDDEN,
            )
        volume = get_object_or_404(Volume, pk=pk)
        submissions = (
            AnnotationSubmission.objects.filter(
                task__volume=volume,
                review_status=SubmissionReviewStatus.APPROVED,
            )
            .select_related("annotator", "task")
            .order_by("-submitted_at", "-id")[:50]
        )
        return Response(
            {
                "results": [
                    {
                        "id": submission.pk,
                        "task": submission.task_id,
                        "annotator": (
                            submission.annotator.get_username()
                            if submission.annotator
                            else None
                        ),
                        "source": submission.source,
                        "submitted_at": submission.submitted_at.isoformat(),
                    }
                    for submission in submissions
                ]
            }
        )


class VolumeGoldStandardView(APIView):
    """``PUT /api/volumes/<pk>/gold-standard/`` — manager-only.

    Body: ``{"is_gold_standard": bool, "reference_submission": int | null}``.

    Refuses a reference submission that belongs to a different volume: scoring
    against a foreign reference would compare two unrelated rasters and, when
    their shapes happened to match, produce a confidently wrong number.
    """

    permission_classes = [IsAuthenticated]

    def put(self, request, pk):
        if not quality_metrics_enabled():
            return _DISABLED
        if not is_manager(request.user):
            return Response(
                {"detail": "Manager access required."},
                status=status.HTTP_403_FORBIDDEN,
            )
        volume = get_object_or_404(Volume, pk=pk)
        payload = request.data if isinstance(request.data, dict) else {}

        reference_id = payload.get("reference_submission", ...)
        if reference_id is not ...:
            if reference_id is None:
                volume.reference_submission = None
            else:
                reference = get_object_or_404(
                    AnnotationSubmission.objects.select_related("task"),
                    pk=reference_id,
                )
                if reference.task.volume_id != volume.pk:
                    return Response(
                        {
                            "detail": (
                                "The reference submission belongs to a "
                                "different volume."
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                volume.reference_submission = reference

        if "is_gold_standard" in payload:
            volume.is_gold_standard = bool(payload["is_gold_standard"])

        if volume.is_gold_standard and volume.reference_submission_id is None:
            return Response(
                {
                    "detail": (
                        "A gold-standard volume needs a reference submission "
                        "to score against."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        volume.save(update_fields=["is_gold_standard", "reference_submission"])
        return Response(
            {
                "volume": volume.pk,
                "is_gold_standard": volume.is_gold_standard,
                "reference_submission": volume.reference_submission_id,
            }
        )


class AnnotatorQualityView(APIView):
    """``GET /api/people/<username>/quality/`` — one person's rolling score.

    Returns ``score: null`` for somebody who has never been measured. The
    frontend must render that as "—", never as a zero, which is why the value
    is nullable all the way out to the wire.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        if not quality_metrics_enabled():
            return _DISABLED
        from django.contrib.auth import get_user_model

        user = get_object_or_404(get_user_model(), username=username)
        scores = (
            QualityScore.objects.filter(
                submission__annotator=user, dice__isnull=False
            )
            .select_related("submission")
            .order_by("-computed_at", "-id")[:50]
        )
        return Response(
            {
                "username": username,
                "score": annotator_quality(user),
                "history": [
                    {
                        "kind": score.kind,
                        "dice": score.dice,
                        "instance_f1": score.instance_f1,
                        "computed_at": score.computed_at.isoformat(),
                    }
                    for score in scores
                ],
            }
        )
