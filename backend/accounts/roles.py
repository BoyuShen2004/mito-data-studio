"""Role predicates.

Role is read from the user's :class:`UserProfile`. Superusers are always
treated as managers so the admin-created superuser can drive the workflow.

API-level enforcement uses the DRF permission classes in
``core.permissions``; these helpers are the shared predicates behind them.
"""

from core.choices import UserRole


def get_role(user) -> str | None:
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return UserRole.MANAGER
    profile = getattr(user, "profile", None)
    return profile.role if profile else None


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
