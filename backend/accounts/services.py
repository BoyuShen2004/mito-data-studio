"""People — who works with whom, derived from projects.

One service, one shape, three role-specific slices (see
``progress/history/05-submit-people-hardcases.md`` goal E). The rule that keeps
this from becoming three unrelated apps: **membership is project-centric**.
Nobody is "assigned to a manager" anywhere in the schema; people are related
because they share a project — the requester who owns it, the manager(s) who
run it, and the annotators holding tasks on it. Every panel below is a
projection of that one relation, and it is the same relation
``annotation.services.is_project_member`` gates hard cases and viewing with.

Managers of a project are read from the project itself (``created_by`` when
that user is a manager, plus ``reviewed_by``); a project nobody has reviewed
yet falls back to "every manager", because that is genuinely who might pick it
up and an annotator asking "who do I hand this to?" deserves an answer rather
than an empty list.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Count, Q

from annotation.models import AnnotationTask, HardCase, ReviewRecord
from core.choices import ACTIVE_TASK_STATUSES, HardCaseStatus, TaskStatus, UserRole
from projects.models import Project

from .models import UserProfile
from .roles import get_role, is_manager, is_requester

User = get_user_model()

# The self-editable half of a profile (``PATCH /api/people/me/``). Role and
# institution *link* stay admin-owned — a user renaming their lab is fine, a
# user promoting themselves to manager is not.
EDITABLE_PROFILE_FIELDS = ("display_name", "institution_name", "contact_note")


def _profile_of(user) -> UserProfile | None:
    return getattr(user, "profile", None)


def person_card(user, *, extra: dict | None = None) -> dict:
    """The shared person shape every People panel returns.

    Deliberately identical across roles so the frontend renders one card
    component; role-specific numbers ride in ``stats``/``projects`` rather
    than in differently-shaped payloads.
    """
    profile = _profile_of(user)
    card = {
        "id": user.id,
        "username": user.get_username(),
        "display_name": (profile.display_name if profile else "") or "",
        "role": get_role(user) or "",
        "institution_name": (profile.institution_name if profile else "") or "",
        "contact_note": (profile.contact_note if profile else "") or "",
        "email": user.email or "",
    }
    if extra:
        card.update(extra)
    return card


def update_own_profile(user, data: dict) -> UserProfile:
    """Apply the editable subset of ``data`` to ``user``'s profile."""
    from .shortcuts import may_customize_annotate_shortcuts

    profile = _profile_of(user) or UserProfile.objects.create(user=user)
    changed = []
    for field in EDITABLE_PROFILE_FIELDS:
        if field in data:
            setattr(profile, field, (data[field] or "").strip())
            changed.append(field)
    if "annotate_shortcuts" in data:
        # A requester has no annotate tools, so a shortcut map for them would be
        # dead configuration. Refused rather than quietly dropped: silently
        # accepting a setting that can never apply is how a UI ends up lying.
        if not may_customize_annotate_shortcuts(get_role(user)):
            raise ValueError("This role has no annotation tools to bind shortcuts to.")
        profile.annotate_shortcuts = dict(data["annotate_shortcuts"])
        changed.append("annotate_shortcuts")
    if changed:
        profile.save(update_fields=changed)
    return profile


# --- Project ↔ people ------------------------------------------------------

def project_managers(project) -> list:
    """Manager(s) who own ``project``: whoever created it (if a manager) and
    whoever reviewed it. Falls back to every manager when neither applies —
    an unreviewed project has no owner yet, and "nobody" is a worse answer
    than "any of these people"."""
    owners = []
    for candidate in (project.created_by, project.reviewed_by):
        if candidate is not None and is_manager(candidate) and candidate not in owners:
            owners.append(candidate)
    if owners:
        return owners
    return list(
        User.objects.filter(profile__role=UserRole.MANAGER, is_active=True).order_by(
            "username"
        )
    )


def project_requester(project):
    """The customer behind ``project`` — the requester who registered it."""
    creator = project.created_by
    if creator is not None and is_requester(creator):
        return creator
    return None


def projects_for_annotator(user):
    """Projects ``user`` participates in, with or without assigned work."""
    return Project.objects.filter(
        Q(tasks__assigned_to=user) | Q(memberships__user=user)
    ).distinct()


def _project_brief(project, *, extra: dict | None = None) -> dict:
    brief = {
        "id": project.id,
        "title": project.title,
        "dataset": project.dataset,
        "status": project.status,
        "manager_reviewed": project.manager_reviewed,
        "deadline": project.deadline,
    }
    if extra:
        brief.update(extra)
    return brief


# --- Counts ----------------------------------------------------------------

def annotator_task_counts(user, *, projects=None) -> dict:
    """Task counts for one annotator: assigned / active / submitted / approved
    / rejected, plus submissions made and reviews received.

    ``submitted`` counts tasks *currently* awaiting review, while
    ``submissions`` sums ``AnnotationTask.submission_count`` — how many times
    they handed work over, which survives latest-only submission pruning (see
    ``annotation.services._supersede_submissions``).
    """
    qs = AnnotationTask.objects.filter(assigned_to=user)
    if projects is not None:
        qs = qs.filter(project__in=projects)
    row = qs.aggregate(
        assigned=Count("id"),
        active=Count("id", filter=Q(status__in=ACTIVE_TASK_STATUSES)),
        submitted=Count("id", filter=Q(status=TaskStatus.SUBMITTED)),
        approved=Count("id", filter=Q(status=TaskStatus.APPROVED)),
        rejected=Count(
            "id",
            filter=Q(
                status__in=[TaskStatus.REJECTED, TaskStatus.REVISION_REQUESTED]
            ),
        ),
        locked=Count("id", filter=Q(annotation_locked=True)),
    )
    row["submissions"] = sum(qs.values_list("submission_count", flat=True))

    reviews = ReviewRecord.objects.filter(task__assigned_to=user)
    if projects is not None:
        reviews = reviews.filter(task__project__in=projects)
    row["reviews_approved"] = reviews.filter(decision="approved").count()
    row["reviews_rejected"] = reviews.filter(
        decision__in=["rejected", "revision_requested"]
    ).count()
    last = reviews.order_by("-reviewed_at").first()
    row["last_decision"] = last.decision if last else ""
    row["last_decision_at"] = last.reviewed_at if last else None
    return row


def _hard_case_counts(user) -> dict:
    """How many cases this person raised, and how many are still open."""
    mine = HardCase.objects.filter(created_by=user)
    return {
        "hard_cases": mine.count(),
        "hard_cases_open": mine.filter(status=HardCaseStatus.OPEN).count(),
    }


# --- The role-scoped overview ----------------------------------------------

def people_overview(user) -> dict:
    """Everything ``/people`` needs for ``user``, in one round trip.

    Always returns the same top-level keys (empty where a role has no such
    panel) so the client renders panels by "is this list non-empty", not by
    branching on role in three places.
    """
    payload = {
        "me": person_card(user, extra={"stats": {}}),
        "role": get_role(user) or "",
        "managers": [],
        "peers": [],
        "annotators": [],
        "requesters": [],
        "projects": [],
    }

    if is_manager(user):
        payload.update(_manager_overview(user))
    elif is_requester(user):
        payload.update(_requester_overview(user))
    else:
        payload.update(_annotator_overview(user))
    return payload


def _annotator_overview(user) -> dict:
    """An annotator sees the people around their own projects.

    Managers: the owners of the projects they hold tasks on. Peers: the other
    annotators on those same projects (each annotated with which projects they
    share, so "who else is on this?" is answerable per project, not just as a
    flat roster).
    """
    projects = list(projects_for_annotator(user))
    project_ids = [p.id for p in projects]

    managers: dict[int, dict] = {}
    for project in projects:
        for manager in project_managers(project):
            entry = managers.setdefault(
                manager.id, person_card(manager, extra={"projects": []})
            )
            entry["projects"].append(_project_brief(project))

    peers: dict[int, dict] = {}
    peer_tasks = (
        AnnotationTask.objects.filter(project_id__in=project_ids)
        .exclude(assigned_to__isnull=True)
        .exclude(assigned_to=user)
        .select_related("assigned_to", "project")
    )
    for task in peer_tasks:
        peer = task.assigned_to
        entry = peers.setdefault(
            peer.id, person_card(peer, extra={"projects": [], "stats": {}})
        )
        if not any(p["id"] == task.project_id for p in entry["projects"]):
            entry["projects"].append(_project_brief(task.project))
    explicit_peers = (
        User.objects.filter(project_memberships__project_id__in=project_ids)
        .exclude(pk=user.pk)
        .select_related("profile")
        .distinct()
    )
    for peer in explicit_peers:
        entry = peers.setdefault(
            peer.id, person_card(peer, extra={"projects": [], "stats": {}})
        )
        for project in projects:
            if project.memberships.filter(user=peer).exists() and not any(
                p["id"] == project.id for p in entry["projects"]
            ):
                entry["projects"].append(_project_brief(project))
    for entry in peers.values():
        entry["stats"] = {
            "shared_projects": len(entry["projects"]),
        }

    me_stats = annotator_task_counts(user)
    me_stats.update(_hard_case_counts(user))
    return {
        "me": person_card(user, extra={"stats": me_stats}),
        "managers": sorted(managers.values(), key=lambda p: p["username"]),
        "peers": sorted(peers.values(), key=lambda p: p["username"]),
        "projects": [_project_brief(p) for p in projects],
    }


def _manager_overview(user) -> dict:
    """A manager sees both sides of the work: who annotates, and who asked.

    Annotators carry their workload + review record; requesters carry the
    projects they registered, so "which customer is waiting on what" is one
    glance rather than a cross-reference against the project list.
    """
    annotators = []
    annotator_users = (
        User.objects.filter(annotation_tasks__isnull=False)
        .distinct()
        .order_by("username")
    )
    for person in annotator_users:
        if is_manager(person):
            continue
        stats = annotator_task_counts(person)
        stats.update(_hard_case_counts(person))
        annotators.append(
            person_card(
                person,
                extra={
                    "stats": stats,
                    "projects": [
                        _project_brief(p) for p in projects_for_annotator(person)
                    ],
                },
            )
        )
    # Annotators with an account but no work yet still belong on the roster —
    # a manager needs to see who is available, not only who is already busy.
    idle = (
        User.objects.filter(profile__role=UserRole.ANNOTATOR, is_active=True)
        .exclude(id__in=[a["id"] for a in annotators])
        .order_by("username")
    )
    for person in idle:
        stats = annotator_task_counts(person)
        stats.update(_hard_case_counts(person))
        annotators.append(
            person_card(person, extra={"stats": stats, "projects": []})
        )

    requesters = []
    requester_users = User.objects.filter(
        profile__role__in=[UserRole.REQUESTER, UserRole.CLIENT]
    ).order_by("username")
    for person in requester_users:
        owned = Project.objects.filter(created_by=person)
        requesters.append(
            person_card(
                person,
                extra={
                    "projects": [_project_brief(p) for p in owned],
                    "stats": {
                        "projects": owned.count(),
                        "active_projects": owned.exclude(
                            status__in=["completed", "delivered", "cancelled"]
                        ).count(),
                    },
                },
            )
        )

    me_stats = {
        "projects": Project.objects.count(),
        "tasks": AnnotationTask.objects.count(),
        "awaiting_review": AnnotationTask.objects.filter(
            status=TaskStatus.SUBMITTED
        ).count(),
        "open_hard_cases": HardCase.objects.filter(
            status=HardCaseStatus.OPEN
        ).count(),
    }
    return {
        "me": person_card(user, extra={"stats": me_stats}),
        "annotators": sorted(annotators, key=lambda p: p["username"]),
        "requesters": requesters,
        "projects": [_project_brief(p) for p in Project.objects.all()[:50]],
    }


def _requester_overview(user) -> dict:
    """A requester sees their own projects and who is running them (v1 light
    panel, per the brief) — plus the annotators actually working on them, so
    "is anyone on this?" has an answer."""
    owned = list(Project.objects.filter(created_by=user))
    managers: dict[int, dict] = {}
    annotators: dict[int, dict] = {}
    projects = []
    for project in owned:
        owners = project_managers(project)
        for manager in owners:
            entry = managers.setdefault(
                manager.id, person_card(manager, extra={"projects": []})
            )
            entry["projects"].append(_project_brief(project))
        working = User.objects.filter(
            Q(annotation_tasks__project=project)
            | Q(project_memberships__project=project)
        ).distinct()
        for person in working:
            entry = annotators.setdefault(
                person.id, person_card(person, extra={"projects": [], "stats": {}})
            )
            if not any(p["id"] == project.id for p in entry["projects"]):
                entry["projects"].append(_project_brief(project))
        projects.append(
            _project_brief(
                project,
                extra={
                    "managers": [m.get_username() for m in owners],
                    "task_count": project.tasks.count(),
                },
            )
        )

    return {
        "me": person_card(
            user,
            extra={
                "stats": {
                    "projects": len(owned),
                    "tasks": AnnotationTask.objects.filter(
                        project__in=owned
                    ).count(),
                }
            },
        ),
        "managers": sorted(managers.values(), key=lambda p: p["username"]),
        "peers": sorted(annotators.values(), key=lambda p: p["username"]),
        "projects": projects,
    }


def person_detail(viewer, username: str) -> dict | None:
    """Read-only card for one person at ``/people/<username>``.

    Anyone signed in may look someone up — this is a small collaboration tool
    and the roster is already visible on ``/people``; what is *not* exposed
    here is anything the overview doesn't already show.
    """
    person = User.objects.filter(username=username).first()
    if person is None:
        return None
    role = get_role(person)
    extra: dict = {"projects": [], "stats": {}}
    if role == UserRole.MANAGER:
        extra["projects"] = [
            _project_brief(p) for p in Project.objects.filter(reviewed_by=person)
        ]
    elif role in (UserRole.REQUESTER, UserRole.CLIENT):
        extra["projects"] = [
            _project_brief(p) for p in Project.objects.filter(created_by=person)
        ]
    else:
        extra["projects"] = [
            _project_brief(p) for p in projects_for_annotator(person)
        ]
        stats = annotator_task_counts(person)
        stats.update(_hard_case_counts(person))
        extra["stats"] = stats
    return person_card(person, extra=extra)
