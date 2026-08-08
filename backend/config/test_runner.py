"""Test runner that gives the whole test session a throwaway data root.

Why this exists: several test modules POST a ``SimpleUploadedFile`` to
``AnnotationSubmission.label_file``. That FileField uses
``core.storage.MitoDataStorage``, whose location is ``settings.MITO_DATA_ROOT``
— which in a normal checkout is *inside the repository*. So a test module that
does not override ``MITO_DATA_ROOT`` silently deposits stub uploads into the real
``<repo>/data/submissions/`` tree, next to genuine annotation data.

That actually happened: a 4-byte stub TIFF for a test-created task appeared in
the development data root after a suite run, and the same mechanism explains a
batch of 29 identical stubs quarantined earlier. Individually harmless — they are
undecodable placeholders referenced by no database row — but they are real writes
into a real data directory, they make "did the suite touch my data?" answerable
only by forensics, and one of them looks exactly like a genuine submission to
anyone browsing the folder.

``test_submit_loop.py`` already solved this locally with
``override_settings(MITO_DATA_ROOT=_TMP, MEDIA_ROOT=_TMP)``. Solving it per
module means every future test author has to remember; solving it here means none
of them has to, and a forgotten override degrades to a temp directory instead of
the repository.

This sets the *default* only. Tests that override ``MITO_DATA_ROOT`` themselves
continue to win, and tests that assert on the configured value keep working
because they read it from settings rather than hard-coding a path.
``MEDIA_ROOT`` is redirected too, for any FileField on Django's default storage.

Registered via ``TEST_RUNNER`` in settings, so it applies to ``manage.py test``
with no per-test opt-in.
"""

from __future__ import annotations

import shutil
import tempfile

from django.conf import settings
from django.test.runner import DiscoverRunner


class IsolatedDataRootRunner(DiscoverRunner):
    """``DiscoverRunner`` whose default data root is a throwaway directory."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._tmp_root = tempfile.mkdtemp(prefix="mito-test-dataroot-")
        self._saved = {
            "MITO_DATA_ROOT": settings.MITO_DATA_ROOT,
            "MEDIA_ROOT": settings.MEDIA_ROOT,
        }
        # MitoDataStorage reads MITO_DATA_ROOT on every access (see
        # core/storage.py), so no storage cache needs invalidating here.
        settings.MITO_DATA_ROOT = self._tmp_root
        settings.MEDIA_ROOT = self._tmp_root

    def teardown_test_environment(self, **kwargs):
        for name, value in getattr(self, "_saved", {}).items():
            setattr(settings, name, value)
        shutil.rmtree(getattr(self, "_tmp_root", ""), ignore_errors=True)
        super().teardown_test_environment(**kwargs)
