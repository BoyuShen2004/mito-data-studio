"""Shared building blocks for the Manager Admin.

Access control reuses the project's role helpers (:func:`accounts.roles.is_manager`)
rather than inventing a second permission system, so a user who is a manager in
the app is a manager in the admin. Superusers are always managers via
``get_role``.
"""

from __future__ import annotations

from django.urls import reverse
from django.utils.html import format_html
from django.utils.http import urlencode

from accounts.roles import is_manager


def changelist_url(model, **params) -> str:
    """URL of ``model``'s admin changelist, optionally filtered by ``params``."""
    url = reverse(
        f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist"
    )
    if params:
        url += "?" + urlencode(params)
    return url


def change_url(obj) -> str:
    """URL of ``obj``'s admin change page."""
    return reverse(
        f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
        args=[obj.pk],
    )


def admin_link(obj, text=None) -> str:
    """A safe ``<a>`` to ``obj``'s change page, or an em dash when ``obj`` is None."""
    if obj is None:
        return "—"
    return format_html('<a href="{}">{}</a>', change_url(obj), text or str(obj))


def count_link(model, text, **params) -> str:
    """A safe ``<a>`` to a filtered changelist, labelled ``text``."""
    return format_html('<a href="{}">{}</a>', changelist_url(model, **params), text)


class ManagerAdminAccessMixin:
    """Grants managers access to a ModelAdmin via the app's role system.

    Staff users without the manager role (and non-staff users) get no access.
    Destructive deletes default to superuser-only; set ``manager_can_delete`` to
    allow the owning ModelAdmin to permit manager deletes (still overridable
    per-object via ``has_delete_permission``).
    """

    manager_can_add = True
    manager_can_change = True
    manager_can_delete = False

    @staticmethod
    def _is_manager(request) -> bool:
        user = request.user
        return bool(user and user.is_active and is_manager(user))

    def has_module_permission(self, request):
        return self._is_manager(request)

    def has_view_permission(self, request, obj=None):
        return self._is_manager(request)

    def has_add_permission(self, request):
        return self._is_manager(request) and self.manager_can_add

    def has_change_permission(self, request, obj=None):
        return self._is_manager(request) and self.manager_can_change

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return self._is_manager(request) and self.manager_can_delete


class NumericIdSearchMixin:
    """Let admin search match a numeric primary key of ``id_search_field``.

    Admin ``search_fields`` can't cleanly match an integer PK with ``icontains``
    across backends; this adds an exact-id match when the query is all digits.
    """

    id_search_field = "pk"

    def get_search_results(self, request, queryset, search_term):
        result, may_have_duplicates = super().get_search_results(
            request, queryset, search_term
        )
        term = (search_term or "").strip()
        if term.isdigit():
            result |= self.model.objects.filter(**{self.id_search_field: int(term)})
        return result, may_have_duplicates


def lock_rows(queryset):
    """Row-lock the objects a changelist selected, without their aggregates.

    Admin ``get_queryset`` overrides commonly ``annotate(Count(...))`` to render
    summary columns, which puts a ``GROUP BY`` in the query. PostgreSQL rejects
    ``SELECT ... FOR UPDATE`` on a grouped query outright:

        FeatureNotSupported: FOR UPDATE is not allowed with GROUP BY clause

    SQLite hid this for years because ``select_for_update()`` is a no-op there,
    so the admin actions looked fine while taking no locks at all.

    Re-selecting by primary key drops the annotations and locks exactly the rows
    the action is about to modify — which is what the caller wanted in the first
    place; the counts were only ever for display.
    """
    ids = list(queryset.values_list("pk", flat=True))
    return queryset.model._default_manager.filter(pk__in=ids).select_for_update()
