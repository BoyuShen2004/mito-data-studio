"""File storage anchored at ``settings.MITO_DATA_ROOT``.

Uploaded image volumes, labels, and submission files are written under the
configured HPC/server data root. The database stores paths relative to this
root (via FileField ``name``), never the large image data itself.

``location`` is resolved dynamically from settings on each access so that
``override_settings(MITO_DATA_ROOT=...)`` works in tests.
"""

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class MitoDataStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        kwargs.pop("location", None)
        super().__init__(*args, **kwargs)

    @property
    def base_location(self):
        return str(settings.MITO_DATA_ROOT)

    @property
    def location(self):
        import os

        return os.path.abspath(self.base_location)


# Singleton storage instance used by FileFields across the project.
mito_data_storage = MitoDataStorage()


def get_mito_storage():
    """Callable passed to FileField ``storage=`` so migrations serialize this
    reference rather than a machine-specific absolute path."""
    return mito_data_storage
