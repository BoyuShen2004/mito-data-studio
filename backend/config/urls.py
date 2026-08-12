"""URL configuration for the Mito Data Studio project.

The React SPA (under ``/api/``) serves annotators and requesters. Managers run
their full daily workflow through the Manager Admin at ``/admin/`` (see
``core.admin_site.ManagerAdminSite`` and ``progress/admin.md``).
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from rest_framework.routers import DefaultRouter

from .views import index, spa_index
from accounts.api import (
    AnnotatorListView,
    LoginView,
    MockLoginView,
    LogoutView,
    MeView,
    MyProfileView,
    PeopleOverviewView,
    PersonDetailView,
    RegisterView,
)
from accounts.collaboration_api import CollaborationAdminView
from annotation.api import (
    AssignmentPlanApplyView,
    AssignmentPlanPreviewView,
    AssignmentPlanRowsView,
    AssignTaskView,
    AssignTasksView,
    HardCaseCreateView,
    HardCaseDetailView,
    HardCaseListView,
    HardCaseMessagesView,
    HardCaseNoteView,
    HardCaseRevokeView,
    HardCaseStatusView,
    MyCompletedTasksView,
    MyTasksView,
    ProjectTasksView,
    PublicHardCaseLabelIdsView,
    PublicHardCaseLabelStateView,
    PublicHardCaseChunkCapabilitiesView,
    PublicHardCaseChunkTokenView,
    PublicHardCaseLabels3DMeshView,
    PublicHardCaseLabels3DView,
    PublicHardCaseLabelsSummaryView,
    PublicHardCaseRegionLabelIdsView,
    PublicHardCaseMetaView,
    PublicHardCaseRegionIndexView,
    PublicHardCaseRegionMaskSliceView,
    PublicHardCaseSliceView,
    PublicTaskShareLabelIdsView,
    PublicTaskShareLabelStateView,
    PublicTaskShareChunkCapabilitiesView,
    PublicTaskShareChunkTokenView,
    PublicTaskShareLabels3DMeshView,
    PublicTaskShareLabels3DView,
    PublicTaskShareLabelsSummaryView,
    PublicTaskShareRegionLabelIdsView,
    PublicTaskShareMetaView,
    PublicTaskShareRegionIndexView,
    PublicTaskShareRegionMaskSliceView,
    PublicTaskShareSliceView,
    PublicHierarchyShareMetaView,
    PublicHierarchyShareSliceView,
    PublicHierarchyShareRegionIndexView,
    PublicHierarchyShareRegionMaskSliceView,
    PublicHierarchyShareLabelStateView,
    PublicHierarchyShareLabelIdsView,
    PublicHierarchyShareLabelsSummaryView,
    PublicHierarchyShareRegionLabelIdsView,
    PublicHierarchyShareLabels3DView,
    PublicHierarchyShareChunkCapabilitiesView,
    PublicHierarchyShareChunkTokenView,
    PublicHierarchyShareLabels3DMeshView,
    ReviewSubmissionView,
    SubmissionDetailView,
    SubmissionListView,
    SubmitInappTaskView,
    SubmitTaskView,
    TaskAnnotationLockView,
    TaskDetailView,
    TaskInterpolateView,
    TaskFloodFillView,
    TaskLabelIdsView,
    TaskLabelLifecycleView,
    TaskLabelStateView,
    TaskLabels3DMeshView,
    TaskLabels3DView,
    TaskLabelsSummaryView,
    TaskRegionLabelIdsView,
    TaskResetLabelsView,
    TaskPredictMaskView,
    TaskPublicShareView,
    TaskTrackView,
    TaskTrackBatchView,
    TaskTrackReviewView,
    TaskTrackingPromptsView,
    TaskVisualizationView,
    TaskWarmEmbeddingView,
    TaskMergeLabelsView,
    TaskDeleteLabelPlanView,
    TaskSplitComponentsView,
    TaskWatershedView,
    VolumeLabelSliceView,
    VolumeMetaView,
    VolumeRegionIndexView,
    VolumeRegionMaskSliceView,
    VolumeSliceView,
)
from volumes.chunks_api import (
    ChunkMetricsView,
    SignedChunkReadView,
    VolumeChunkCapabilitiesView,
    VolumeChunkReadView,
    VolumeChunkTokenView,
)
from core.deployment_api import DeploymentIdentityView, DeploymentReleaseView
from core.reset_api import (
    DevelopmentResetView,
    ResetConfirmView,
    ResetExecuteView,
    ResetStatusView,
)
from core.observability import healthz, metrics, readyz
from core.statistics_api import (
    AnnotatorStatisticsView,
    ProjectStatisticsCsvView,
    ProjectStatisticsView,
)
from processing.api import ProcessingJobViewSet
from projects.api import DatasetViewSet, ProjectViewSet
from projects.share_api import PublicShareAdminView, PublicShareBrowseView, PublicShareEntityView, PublicShareRevokeView, PublicShareTreeView
from volumes.api import (
    HpcScanView,
    ProjectVolumesView,
    RegisterDataView,
    VolumeDependentsView,
    VolumeDetailView,
    VolumePyramidBuildView,
)

# ProjectViewSet handles /api/projects/ CRUD plus a progress summary action.
router = DefaultRouter()
router.register("projects", ProjectViewSet, basename="project")
# A project holds many datasets; a dataset holds many volume pairs.
router.register("datasets", DatasetViewSet, basename="dataset")
router.register("processing-jobs", ProcessingJobViewSet, basename="processing-job")

# Dev: no frontend/dist build, so "/" is a friendly landing page pointing at
# the separate Vite dev server. Prod: `npm run build` produced frontend/dist,
# so "/" (and any unmatched path below) serves the built SPA instead.
_root_view = spa_index if settings.FRONTEND_DIST.exists() else index

urlpatterns = [
    path("", _root_view, name="index"),
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    path("metrics", metrics, name="metrics"),
    path("admin/", admin.site.urls),
    # --- Auth --------------------------------------------------------------
    path("api/auth/login/", LoginView.as_view(), name="api-login"),
    path("api/auth/mock-login/", MockLoginView.as_view(), name="api-mock-login"),
    path("api/auth/development-reset/", DevelopmentResetView.as_view(), name="api-development-reset"),
    path("api/auth/logout/", LogoutView.as_view(), name="api-logout"),
    path("api/auth/me/", MeView.as_view(), name="api-me"),
    path("api/admin/reset/status/", ResetStatusView.as_view(), name="api-reset-status"),
    path("api/admin/reset/confirm/", ResetConfirmView.as_view(), name="api-reset-confirm"),
    path("api/admin/reset/execute/", ResetExecuteView.as_view(), name="api-reset-execute"),
    path("api/auth/register/", RegisterView.as_view(), name="api-register"),
    path("api/annotators/", AnnotatorListView.as_view(), name="api-annotators"),
    path("api/collaboration/", CollaborationAdminView.as_view(), name="api-collaboration"),
    path("api/public-shares/", PublicShareAdminView.as_view(), name="api-public-shares"),
    path("api/public-shares/tree/", PublicShareTreeView.as_view(), name="api-public-share-tree"),
    path("api/public-shares/entity/", PublicShareEntityView.as_view(), name="api-public-share-entity"),
    path("api/public-shares/<int:pk>/revoke/", PublicShareRevokeView.as_view(), name="api-public-share-revoke"),
    path("api/public/shares/<str:token>/", PublicShareBrowseView.as_view(), name="api-public-share-browse"),
    path("api/public/shares/<str:token>/volumes/<int:volume_id>/meta/", PublicHierarchyShareMetaView.as_view()),
    path("api/public/shares/<str:token>/volumes/<int:volume_id>/slice/", PublicHierarchyShareSliceView.as_view()),
    path("api/public/shares/<str:token>/volumes/<int:volume_id>/region-mask-slice/", PublicHierarchyShareRegionMaskSliceView.as_view()),
    path("api/public/shares/<str:token>/volumes/<int:volume_id>/region-index/", PublicHierarchyShareRegionIndexView.as_view()),
    path("api/public/shares/<str:token>/volumes/<int:volume_id>/label-state/", PublicHierarchyShareLabelStateView.as_view()),
    path("api/public/shares/<str:token>/volumes/<int:volume_id>/label-ids/", PublicHierarchyShareLabelIdsView.as_view()),
    path("api/public/shares/<str:token>/volumes/<int:volume_id>/labels-summary/", PublicHierarchyShareLabelsSummaryView.as_view()),
    path("api/public/shares/<str:token>/volumes/<int:volume_id>/region-label-ids/", PublicHierarchyShareRegionLabelIdsView.as_view()),
    path("api/public/shares/<str:token>/volumes/<int:volume_id>/labels-3d/", PublicHierarchyShareLabels3DView.as_view()),
    path("api/public/shares/<str:token>/volumes/<int:volume_id>/labels-3d-mesh/", PublicHierarchyShareLabels3DMeshView.as_view()),
    path("api/public/shares/<str:token>/volumes/<int:volume_id>/chunks/capabilities/", PublicHierarchyShareChunkCapabilitiesView.as_view(), name="api-public-share-chunk-capabilities"),
    path("api/public/shares/<str:token>/volumes/<int:volume_id>/chunks/token/", PublicHierarchyShareChunkTokenView.as_view(), name="api-public-share-chunk-token"),
    # --- People (collaboration surface, all roles) -------------------------
    path("api/people/overview/", PeopleOverviewView.as_view(), name="api-people-overview"),
    path("api/people/me/", MyProfileView.as_view(), name="api-people-me"),
    path(
        "api/people/<str:username>/",
        PersonDetailView.as_view(),
        name="api-person-detail",
    ),
    # --- Data registration (requesters + managers, shared endpoint) --------
    path("api/register-data/", RegisterDataView.as_view(), name="api-register-data"),
    path("api/hpc/scan/", HpcScanView.as_view(), name="api-hpc-scan"),
    # --- Volumes (project-nested + detail) ---------------------------------
    path(
        "api/projects/<int:project_id>/volumes/",
        ProjectVolumesView.as_view(),
        name="api-project-volumes",
    ),
    path("api/volumes/<int:pk>/", VolumeDetailView.as_view(), name="api-volume-detail"),
    path(
        "api/volumes/<int:pk>/dependents/",
        VolumeDependentsView.as_view(),
        name="api-volume-dependents",
    ),
    path(
        "api/volumes/<int:pk>/pyramid/",
        VolumePyramidBuildView.as_view(),
        name="api-volume-pyramid-build",
    ),
    # --- Tasks -------------------------------------------------------------
    path(
        "api/projects/<int:project_id>/tasks/",
        ProjectTasksView.as_view(),
        name="api-project-tasks",
    ),
    path(
        "api/projects/<int:project_id>/assign-tasks/",
        AssignTasksView.as_view(),
        name="api-assign-tasks",
    ),
    path(
        "api/projects/<int:project_id>/assign-plan/rows/",
        AssignmentPlanRowsView.as_view(),
        name="api-assign-plan-rows",
    ),
    path(
        "api/projects/<int:project_id>/assign-plan/preview/",
        AssignmentPlanPreviewView.as_view(),
        name="api-assign-plan-preview",
    ),
    path(
        "api/projects/<int:project_id>/assign-plan/apply/",
        AssignmentPlanApplyView.as_view(),
        name="api-assign-plan-apply",
    ),
    # --- Phase 12: chunk/datastore service ------------------------------------
    # Registered unconditionally; each returns 503 unless FEATURE_CHUNK_SERVICE
    # and FEATURE_VOLUME_PYRAMIDS are both on. Endpoints must not appear merely
    # because pyramid files exist on disk (ADR-010 §8).
    path(
        "api/volumes/<int:pk>/chunks/capabilities/",
        VolumeChunkCapabilitiesView.as_view(),
        name="api-volume-chunk-capabilities",
    ),
    path(
        "api/volumes/<int:pk>/chunks/token/",
        VolumeChunkTokenView.as_view(),
        name="api-volume-chunk-token",
    ),
    path(
        "api/volumes/<int:pk>/chunks/<str:mag>/<int:cz>/<int:cy>/<int:cx>/",
        VolumeChunkReadView.as_view(),
        name="api-volume-chunk-read",
    ),
    # Token path: the volume comes from the signed claims, never the URL.
    path(
        "api/chunks/signed/<str:mag>/<int:cz>/<int:cy>/<int:cx>/",
        SignedChunkReadView.as_view(),
        name="api-chunk-signed-read",
    ),
    path("api/chunks/metrics/", ChunkMetricsView.as_view(), name="api-chunk-metrics"),
    # --- end Phase 12 ---------------------------------------------------------
    # --- Deployment identity -------------------------------------------------
    # Never behind a feature flag: this is the endpoint you call to find out
    # whether a flag (or anything else) is being read from the instance you
    # think you are talking to. Authenticated, read-only, no secrets.
    path(
        "api/deployment/identity/",
        DeploymentIdentityView.as_view(),
        name="api-deployment-identity",
    ),
    path(
        "api/deployment/release/",
        DeploymentReleaseView.as_view(),
        name="api-deployment-release",
    ),
    # --- Phase 6: dashboards & statistics -----------------------------------
    # Read-only. Registered unconditionally; each returns 503 unless
    # FEATURE_DASHBOARDS is on, so a misconfiguration reads as "not enabled"
    # rather than a 404 typo.
    path(
        "api/statistics/project/<int:pk>/",
        ProjectStatisticsView.as_view(),
        name="api-statistics-project",
    ),
    path(
        "api/statistics/project/<int:pk>/export/",
        ProjectStatisticsCsvView.as_view(),
        name="api-statistics-project-export",
    ),
    path(
        "api/statistics/annotators/",
        AnnotatorStatisticsView.as_view(),
        name="api-statistics-annotators",
    ),
    # --- end Phase 6 --------------------------------------------------------
    path("api/tasks/<int:pk>/", TaskDetailView.as_view(), name="api-task-detail"),
    path(
        "api/tasks/<int:pk>/visualization/",
        TaskVisualizationView.as_view(),
        name="api-task-visualization",
    ),
    # --- Slice streaming + in-app annotation -------------------------------
    path(
        "api/volumes/<int:pk>/meta/",
        VolumeMetaView.as_view(),
        name="api-volume-meta",
    ),
    path(
        "api/volumes/<int:pk>/slice/",
        VolumeSliceView.as_view(),
        name="api-volume-slice",
    ),
    path(
        "api/volumes/<int:pk>/label-slice/",
        VolumeLabelSliceView.as_view(),
        name="api-volume-label-slice",
    ),
    path(
        "api/volumes/<int:pk>/region-mask-slice/",
        VolumeRegionMaskSliceView.as_view(),
        name="api-volume-region-mask-slice",
    ),
    path(
        "api/volumes/<int:pk>/region-index/",
        VolumeRegionIndexView.as_view(),
        name="api-volume-region-index",
    ),
    path(
        "api/tasks/<int:pk>/track/",
        TaskTrackView.as_view(),
        name="api-task-track",
    ),
    path(
        "api/tasks/<int:pk>/track/prompts/",
        TaskTrackingPromptsView.as_view(),
        name="api-task-tracking-prompts",
    ),
    path(
        "api/tasks/<int:pk>/track/batch/",
        TaskTrackBatchView.as_view(),
        name="api-task-track-batch",
    ),
    path(
        "api/tasks/<int:pk>/track/review/",
        TaskTrackReviewView.as_view(),
        name="api-task-track-review",
    ),
    path(
        "api/tasks/<int:pk>/label-state/",
        TaskLabelStateView.as_view(),
        name="api-task-label-state",
    ),
    path(
        "api/tasks/<int:pk>/label-ids/",
        TaskLabelIdsView.as_view(),
        name="api-task-label-ids",
    ),
    path(
        "api/tasks/<int:pk>/predict-mask/",
        TaskPredictMaskView.as_view(),
        name="api-task-predict-mask",
    ),
    path(
        "api/tasks/<int:pk>/warm-embedding/",
        TaskWarmEmbeddingView.as_view(),
        name="api-task-warm-embedding",
    ),
    path(
        "api/tasks/<int:pk>/watershed/",
        TaskWatershedView.as_view(),
        name="api-task-watershed",
    ),
    path(
        "api/tasks/<int:pk>/split-components/",
        TaskSplitComponentsView.as_view(),
        name="api-task-split-components",
    ),
    path(
        "api/tasks/<int:pk>/merge-labels/",
        TaskMergeLabelsView.as_view(),
        name="api-task-merge-labels",
    ),
    path(
        "api/tasks/<int:pk>/delete-label-plan/",
        TaskDeleteLabelPlanView.as_view(),
        name="api-task-delete-label-plan",
    ),
    # Registered unconditionally; the view returns 503 unless
    # FEATURE_INTERPOLATION (and, for an apply, FEATURE_ANNOTATION_OPS) is on.
    # Routing that appears and disappears with a flag makes "is it enabled?"
    # indistinguishable from "is it deployed?".
    path(
        "api/tasks/<int:pk>/interpolate/",
        TaskInterpolateView.as_view(),
        name="api-task-interpolate",
    ),
    path(
        "api/tasks/<int:pk>/flood-fill/",
        TaskFloodFillView.as_view(),
        name="api-task-flood-fill",
    ),
    path(
        "api/tasks/<int:pk>/labels-summary/",
        TaskLabelsSummaryView.as_view(),
        name="api-task-labels-summary",
    ),
    path(
        "api/tasks/<int:pk>/region-label-ids/",
        TaskRegionLabelIdsView.as_view(),
        name="api-task-region-label-ids",
    ),
    path(
        "api/tasks/<int:pk>/labels/reset/",
        TaskResetLabelsView.as_view(),
        name="api-task-labels-reset",
    ),
    path(
        "api/tasks/<int:pk>/labels-3d/",
        TaskLabels3DView.as_view(),
        name="api-task-labels-3d",
    ),
    path(
        "api/tasks/<int:pk>/labels-3d-mesh/",
        TaskLabels3DMeshView.as_view(),
        name="api-task-labels-3d-mesh",
    ),
    path(
        "api/tasks/<int:pk>/labels/<int:label_id>/lifecycle/",
        TaskLabelLifecycleView.as_view(),
        name="api-task-label-lifecycle",
    ),
    # --- Hard cases: project-scoped (auth) + public read-only (token) ------
    path(
        "api/tasks/<int:pk>/hard-cases/",
        HardCaseCreateView.as_view(),
        name="api-task-hard-case-create",
    ),
    path(
        "api/tasks/<int:pk>/share/",
        TaskPublicShareView.as_view(),
        name="api-task-public-share",
    ),
    path("api/hard-cases/", HardCaseListView.as_view(), name="api-hard-cases"),
    path(
        "api/hard-cases/<int:pk>/",
        HardCaseDetailView.as_view(),
        name="api-hard-case-detail",
    ),
    path(
        "api/hard-cases/<int:pk>/status/",
        HardCaseStatusView.as_view(),
        name="api-hard-case-status",
    ),
    path(
        "api/hard-cases/<int:pk>/note/",
        HardCaseNoteView.as_view(),
        name="api-hard-case-note",
    ),
    path(
        "api/hard-cases/<int:pk>/messages/",
        HardCaseMessagesView.as_view(),
        name="api-hard-case-messages",
    ),
    path(
        "api/hard-cases/<int:pk>/revoke/",
        HardCaseRevokeView.as_view(),
        name="api-hard-case-revoke",
    ),
    path(
        "api/public/hard-cases/<str:token>/meta/",
        PublicHardCaseMetaView.as_view(),
        name="api-public-hard-case-meta",
    ),
    path(
        "api/public/hard-cases/<str:token>/slice/",
        PublicHardCaseSliceView.as_view(),
        name="api-public-hard-case-slice",
    ),
    path(
        "api/public/hard-cases/<str:token>/region-mask-slice/",
        PublicHardCaseRegionMaskSliceView.as_view(),
        name="api-public-hard-case-region-mask-slice",
    ),
    path(
        "api/public/hard-cases/<str:token>/region-index/",
        PublicHardCaseRegionIndexView.as_view(),
        name="api-public-hard-case-region-index",
    ),
    path(
        "api/public/hard-cases/<str:token>/label-state/",
        PublicHardCaseLabelStateView.as_view(),
        name="api-public-hard-case-label-state",
    ),
    path(
        "api/public/hard-cases/<str:token>/label-ids/",
        PublicHardCaseLabelIdsView.as_view(),
        name="api-public-hard-case-label-ids",
    ),
    path(
        "api/public/hard-cases/<str:token>/labels-summary/",
        PublicHardCaseLabelsSummaryView.as_view(),
        name="api-public-hard-case-labels-summary",
    ),
    path(
        "api/public/hard-cases/<str:token>/region-label-ids/",
        PublicHardCaseRegionLabelIdsView.as_view(),
        name="api-public-hard-case-region-label-ids",
    ),
    path(
        "api/public/hard-cases/<str:token>/labels-3d/",
        PublicHardCaseLabels3DView.as_view(),
        name="api-public-hard-case-labels-3d",
    ),
    path(
        "api/public/hard-cases/<str:token>/labels-3d-mesh/",
        PublicHardCaseLabels3DMeshView.as_view(),
        name="api-public-hard-case-labels-3d-mesh",
    ),
    path(
        "api/public/hard-cases/<str:token>/chunks/capabilities/",
        PublicHardCaseChunkCapabilitiesView.as_view(),
        name="api-public-hard-case-chunk-capabilities",
    ),
    path(
        "api/public/hard-cases/<str:token>/chunks/token/",
        PublicHardCaseChunkTokenView.as_view(),
        name="api-public-hard-case-chunk-token",
    ),
    path(
        "api/public/tasks/<str:token>/meta/",
        PublicTaskShareMetaView.as_view(),
        name="api-public-task-share-meta",
    ),
    path(
        "api/public/tasks/<str:token>/slice/",
        PublicTaskShareSliceView.as_view(),
        name="api-public-task-share-slice",
    ),
    path(
        "api/public/tasks/<str:token>/region-mask-slice/",
        PublicTaskShareRegionMaskSliceView.as_view(),
        name="api-public-task-share-region-mask-slice",
    ),
    path(
        "api/public/tasks/<str:token>/region-index/",
        PublicTaskShareRegionIndexView.as_view(),
        name="api-public-task-share-region-index",
    ),
    path(
        "api/public/tasks/<str:token>/label-state/",
        PublicTaskShareLabelStateView.as_view(),
        name="api-public-task-share-label-state",
    ),
    path(
        "api/public/tasks/<str:token>/label-ids/",
        PublicTaskShareLabelIdsView.as_view(),
        name="api-public-task-share-label-ids",
    ),
    path(
        "api/public/tasks/<str:token>/labels-summary/",
        PublicTaskShareLabelsSummaryView.as_view(),
        name="api-public-task-share-labels-summary",
    ),
    path(
        "api/public/tasks/<str:token>/region-label-ids/",
        PublicTaskShareRegionLabelIdsView.as_view(),
        name="api-public-task-share-region-label-ids",
    ),
    path(
        "api/public/tasks/<str:token>/labels-3d/",
        PublicTaskShareLabels3DView.as_view(),
        name="api-public-task-share-labels-3d",
    ),
    path(
        "api/public/tasks/<str:token>/labels-3d-mesh/",
        PublicTaskShareLabels3DMeshView.as_view(),
        name="api-public-task-share-labels-3d-mesh",
    ),
    path(
        "api/public/tasks/<str:token>/chunks/capabilities/",
        PublicTaskShareChunkCapabilitiesView.as_view(),
        name="api-public-task-share-chunk-capabilities",
    ),
    path(
        "api/public/tasks/<str:token>/chunks/token/",
        PublicTaskShareChunkTokenView.as_view(),
        name="api-public-task-share-chunk-token",
    ),
    path(
        "api/tasks/<int:pk>/assign/",
        AssignTaskView.as_view(),
        name="api-task-assign",
    ),
    path(
        "api/tasks/<int:pk>/annotation-lock/",
        TaskAnnotationLockView.as_view(),
        name="api-task-annotation-lock",
    ),
    path(
        "api/tasks/<int:pk>/submit/",
        SubmitTaskView.as_view(),
        name="api-task-submit",
    ),
    path(
        "api/tasks/<int:pk>/submit-inapp/",
        SubmitInappTaskView.as_view(),
        name="api-task-submit-inapp",
    ),
    path("api/my-tasks/", MyTasksView.as_view(), name="api-my-tasks"),
    path(
        "api/my-completed-tasks/",
        MyCompletedTasksView.as_view(),
        name="api-my-completed-tasks",
    ),
    # --- Submissions -------------------------------------------------------
    path("api/submissions/", SubmissionListView.as_view(), name="api-submissions"),
    path(
        "api/submissions/<int:pk>/",
        SubmissionDetailView.as_view(),
        name="api-submission-detail",
    ),
    path(
        "api/submissions/<int:pk>/review/",
        ReviewSubmissionView.as_view(),
        name="api-submission-review",
    ),
    # --- Project CRUD + summary (router) -----------------------------------
    path("api/", include(router.urls)),
]

# Uploaded task/submission images — needed in prod too, not just DEBUG (there
# is no nginx/whitenoise handling for MEDIA_ROOT, only STATIC_ROOT).
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# SPA catch-all: any GET that isn't api/admin/static/media/assets falls through
# to the built frontend's index.html so React Router's client-side routes
# survive a hard refresh. No-op in dev (frontend/dist doesn't exist there).
# `/assets/` must NOT hit this — Vite emits JS/CSS there; serving index.html
# for those URLs white-screens the SPA. WhiteNoise normally serves them from
# WHITENOISE_ROOT, but after a rebuild without worker restart the index can be
# stale; an explicit on-disk serve keeps deploys safe.
if settings.FRONTEND_DIST.exists():
    from django.views.static import serve as static_serve

    _frontend_assets = settings.FRONTEND_DIST / "assets"
    urlpatterns += [
        re_path(
            r"^assets/(?P<path>.*)$",
            static_serve,
            {"document_root": _frontend_assets},
            name="frontend-assets",
        ),
        re_path(
            r"^(?!api/|admin/|static/|media/|assets/).*$",
            spa_index,
            name="spa-fallback",
        ),
    ]
