"""Role predicates.

Durable roles come from :class:`UserProfile`; token authentication may select
an authorized workspace for the current request. Superusers are managers.

API-level enforcement uses the DRF permission classes in
``core.permissions``; these helpers are the shared predicates behind them.
"""

from core.choices import UserRole


def base_role(user) -> str | None:
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return UserRole.MANAGER
    profile = getattr(user, "profile", None)
    return profile.role if profile else None


def available_roles(user) -> list[str]:
    role = base_role(user)
    if role == UserRole.ANNOTATOR and getattr(getattr(user, "profile", None), "is_assistant_manager", False):
        return [UserRole.ANNOTATOR, UserRole.MANAGER]
    return [role] if role else []


def is_primary_manager(user) -> bool:
    return base_role(user) == UserRole.MANAGER


def has_manager_role(user) -> bool:
    """Durable membership, independent of this request's selected workspace."""
    return UserRole.MANAGER in available_roles(user)


def get_role(user) -> str | None:
    return getattr(user, "_active_role", None) or base_role(user)


def is_manager(user) -> bool:
    return get_role(user) == UserRole.MANAGER


def is_annotator(user) -> bool:
    role = get_role(user)
    # Managers can also view annotator pages; annotators cannot view manager pages.
    return role in (UserRole.ANNOTATOR, UserRole.MANAGER)


def is_requester(user) -> bool:
    # The legacy ``client`` role is treated as a requester.
    return get_role(user) in (UserRole.REQUESTER, UserRole.CLIENT)


def can_register_data(user) -> bool:
    """Requesters and managers may register datasets."""
    return is_manager(user) or is_requester(user)
