from django.conf import settings
from django.db import models
import secrets

from core.choices import AnnotationType, ProjectStatus, WorkflowType


class Project(models.Model):
    """An annotation project. Holds one or more datasets (see :class:`Dataset`).

    The hierarchy is project → dataset → volume: a project groups the datasets
    a requester submitted, each dataset groups the image/mask volume pairs
    registered from it.
    """

    title = models.CharField(max_length=255)
    # Legacy single-dataset name, kept so old rows and callers still read.
    # The datasets a project holds now live in the ``datasets`` relation; this
    # mirrors the first one for backwards compatibility.
    dataset = models.CharField(max_length=255, blank=True)
    institution = models.ForeignKey(
        "accounts.Institution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    description = models.TextField(blank=True)
    # Optional biomedical EM metadata that cannot be derived from the files
    # (organism, tissue, cell type, imaging modality, instrument, conditions,
    # source, publication, notes, …). Kept flexible as structured JSON.
    metadata = models.JSONField(default=dict, blank=True)
    annotation_target = models.CharField(max_length=100, default="mitochondria")
    annotation_type = models.CharField(
        max_length=30,
        choices=AnnotationType.choices,
        default=AnnotationType.INSTANCE,
    )
    # The high-level pipeline requested for this dataset (annotation /
    # proofreading / segmentation). Shares the same models and services; drives
    # only configuration and service-layer branching, not a separate pipeline.
    workflow_type = models.CharField(
        max_length=20,
        choices=WorkflowType.choices,
        default=WorkflowType.ANNOTATION,
    )
    status = models.CharField(
        max_length=20, choices=ProjectStatus.choices, default=ProjectStatus.DRAFT
    )
    deadline = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_projects",
    )

    # Manager review gate: requester-registered data must be reviewed by a
    # manager before its volumes can be split or assigned. Manager-registered
    # data is reviewed on creation.
    manager_reviewed = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_projects",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # --- Phase 2: queue controls -------------------------------------------
    # Higher wins when manager auto-fill orders unassigned work. Ties are
    # deterministic in the service layer rather than relying on database order.
    priority = models.IntegerField(default=0)
    # A paused project is skipped by assignment entirely without changing any
    # task's own state — the operational "stop handing this out" switch.
    paused = models.BooleanField(default=False)

    # Phase 1 ACL: which teams may work on this project. Additive — an empty
    # set means "fall back to the legacy creator/assignee rule", so existing
    # projects keep working untouched until they are granted teams. Only
    # consulted when `settings.FEATURE_TEAMS` is on.
    teams = models.ManyToManyField(
        "accounts.Team", related_name="projects", blank=True
    )
    # The one team whose members may receive this project's task assignments.
    # ``teams`` remains the broader access/grant relation; assignment has one
    # explicit owner so managers choose it once, not once per volume.
    working_team = models.ForeignKey(
        "accounts.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="working_projects",
    )

    # Explicit collaboration membership is deliberately independent of task
    # assignment.  A person may participate in project review / Hard Cases
    # without consuming an annotation slot or appearing in workload counts.
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="projects.ProjectMembership",
        through_fields=("project", "user"),
        related_name="member_projects",
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class ProjectMembership(models.Model):
    """A person's explicit, non-workload-bearing access to one project."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_memberships",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="project_memberships_added",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user"], name="unique_project_membership"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.project}"


class Dataset(models.Model):
    """A dataset registered under a project, grouping its volume pairs.

    One project may hold several datasets (e.g. a CellMap set and a MitoEM set),
    and each dataset holds many image/mask volume pairs. Metadata lives here
    rather than on the project because it describes *this* data — organism,
    publication, label classes — and two datasets in one project may differ.
    """

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="datasets"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    # The source directories this dataset was registered from, recorded so the
    # registration can be understood (and repeated) later.
    image_directory = models.CharField(max_length=1024, blank=True)
    region_mask_directory = models.CharField(max_length=1024, blank=True)
    mask_directory = models.CharField(max_length=1024, blank=True)
    # Biomedical + provenance metadata for this dataset (organism, tissue,
    # publication, label_classes, channel_names, split, …).
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        # A dataset name identifies the data within its project.
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"], name="unique_dataset_name_per_project"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.project.title})"


class PublicShare(models.Model):
    """Revocable, database-backed anonymous read access to a hierarchy scope."""

    class Scope(models.TextChoices):
        PROJECT = "project", "Project"
        DATASET = "dataset", "Dataset"
        VOLUME = "volume", "Volume"

    token = models.CharField(max_length=64, unique=True, editable=False)
    scope = models.CharField(max_length=16, choices=Scope.choices)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="public_shares")
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, null=True, blank=True, related_name="public_shares")
    volume = models.ForeignKey("volumes.Volume", on_delete=models.CASCADE, null=True, blank=True, related_name="public_shares")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="created_public_shares")
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="revoked_public_shares")

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)
