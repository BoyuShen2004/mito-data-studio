"""App config that installs the Manager Admin site as the default admin site.

Referenced from ``INSTALLED_APPS`` in place of ``django.contrib.admin`` so that
``django.contrib.admin.site`` is :class:`core.admin_site.ManagerAdminSite`. Kept
in its own module (not ``core/apps.py``) to avoid app-config autodiscovery
picking up two default configs for the ``core`` app.
"""

from django.contrib.admin.apps import AdminConfig


class ManagerAdminConfig(AdminConfig):
    default_site = "core.admin_site.ManagerAdminSite"
