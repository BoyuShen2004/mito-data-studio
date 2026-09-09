"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/wsgi/
"""

import os

from django.conf import settings as _settings
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()


def _preload_mask_model() -> None:
    """Load SAM 2 while the worker boots, not on someone's first click.

    Each gunicorn worker owns its own CUDA model, and loading it takes ~5 s.
    Left lazy, that cost lands on whichever annotator happens to click first
    on each worker — three unexplained multi-second waits after every deploy,
    on exactly the interaction that is supposed to feel instant.

    Runs on a daemon thread so the worker starts serving immediately: health
    checks, slice reads and everything non-AI do not wait for the checkpoint.

    A failure here is *not* recorded as the model's permanent state. Preload
    runs before the GPU is necessarily settled, and a sticky error would turn
    a transient boot-time condition into 503s for the life of the worker; the
    registry is reset so the first real request retries and reports honestly.
    """
    from django.conf import settings

    if not getattr(settings, "MITO_AI_PRELOAD", True):
        return

    import logging
    import threading

    def load() -> None:
        from annotation.cellable_port.ai import registry

        try:
            registry.get_mask_model()
        except Exception as exc:  # noqa: BLE001 - a preload must never crash boot
            registry.reset_mask_model()
            logging.getLogger(__name__).warning(
                "SAM 2 preload failed (%s); the first request will retry.", exc
            )

    threading.Thread(target=load, name="sam2-preload", daemon=True).start()


# Not under `manage.py test`: the suite has no use for a 1 GB CUDA model, and
# a preload thread racing the tests that assert on this function is noise.
if not getattr(_settings, "RUNNING_TESTS", False):
    _preload_mask_model()
