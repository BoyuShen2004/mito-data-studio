"""Loading SAM 2 at worker boot rather than on the first click."""

from __future__ import annotations

import unittest.mock

from django.test import SimpleTestCase, override_settings


class PreloadTests(SimpleTestCase):
    def _run(self):
        """Call the preload and wait for its thread, whatever it did."""
        from config import wsgi

        started = []
        real_thread = __import__("threading").Thread

        def capture(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            started.append(thread)
            return thread

        with unittest.mock.patch("threading.Thread", side_effect=capture):
            wsgi._preload_mask_model()
        for thread in started:
            thread.join(timeout=5)
        return started

    @override_settings(MITO_AI_PRELOAD=True)
    def test_the_model_is_loaded_off_the_request_path(self):
        with unittest.mock.patch(
            "annotation.cellable_port.ai.registry.get_mask_model"
        ) as load:
            threads = self._run()

        load.assert_called_once()
        # On a daemon thread: a worker must start serving health checks and
        # slice reads without waiting for a 1 GB checkpoint.
        self.assertTrue(threads and threads[0].daemon)

    @override_settings(MITO_AI_PRELOAD=False)
    def test_preloading_can_be_turned_off(self):
        with unittest.mock.patch(
            "annotation.cellable_port.ai.registry.get_mask_model"
        ) as load:
            self._run()

        load.assert_not_called()

    @override_settings(MITO_AI_PRELOAD=True)
    def test_a_boot_time_failure_is_not_remembered_as_permanent(self):
        # Preload runs before the GPU has necessarily settled. A sticky error
        # would turn that into 503s for the life of the worker.
        with unittest.mock.patch(
            "annotation.cellable_port.ai.registry.get_mask_model",
            side_effect=RuntimeError("CUDA busy"),
        ), unittest.mock.patch(
            "annotation.cellable_port.ai.registry.reset_mask_model"
        ) as reset:
            self._run()

        reset.assert_called_once()

    @override_settings(MITO_AI_PRELOAD=True)
    def test_a_failure_never_propagates_out_of_boot(self):
        with unittest.mock.patch(
            "annotation.cellable_port.ai.registry.get_mask_model",
            side_effect=RuntimeError("no GPU"),
        ):
            self._run()  # must not raise
