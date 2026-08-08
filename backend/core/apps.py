from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Registers the ``deployment`` system checks (data root ownership,
        # public-exposure settings). Imported for the side effect of the
        # @register decorators — see core/checks.py.
        from . import checks  # noqa: F401
