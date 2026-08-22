"""Regressions for the working-volume write lane (``serialized_file_write``).

These guard properties that are invisible to a single-threaded test run but
decide whether a Gunicorn worker survives contention:

* nesting must not self-deadlock (``flock`` is owned by the open file
  description, so a naive nested acquire blocks against its own outer lock and
  wedges the thread permanently — no exception, no timeout);
* readers must actually be concurrent, otherwise every editor slice read for a
  volume serializes across all workers;
* a writer must still exclude readers.
"""

import tempfile
import threading
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from annotation.visualization.slice_io import serialized_file_write


class SerializedFileWriteTests(SimpleTestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.target = Path(self._dir.name) / "vol" / "working.tif"
        self.target.parent.mkdir(parents=True, exist_ok=True)

    def _run_guarded(self, fn, timeout=5.0):
        """Run ``fn`` on a worker thread; report whether it finished."""
        done = threading.Event()
        error: list[BaseException] = []

        def target():
            try:
                fn()
            except BaseException as exc:  # noqa: BLE001 - reported to the test
                error.append(exc)
            finally:
                done.set()

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        finished = done.wait(timeout)
        if error:
            raise error[0]
        return finished

    def test_nested_exclusive_acquire_does_not_deadlock(self):
        def nested():
            with serialized_file_write(self.target):
                with serialized_file_write(self.target):
                    pass

        self.assertTrue(
            self._run_guarded(nested),
            "nested serialized_file_write deadlocked - flock is per open file "
            "description, so re-entry must be tracked per thread",
        )

    def test_nested_shared_inside_exclusive_does_not_deadlock(self):
        def nested():
            with serialized_file_write(self.target):
                with serialized_file_write(self.target, shared=True):
                    pass

        self.assertTrue(self._run_guarded(nested), "shared-in-exclusive deadlocked")

    def test_lock_is_released_when_the_body_raises(self):
        with self.assertRaises(RuntimeError):
            with serialized_file_write(self.target):
                raise RuntimeError("boom")

        # A leaked hold would wedge this second acquire instead of returning.
        self.assertTrue(
            self._run_guarded(
                lambda: serialized_file_write(self.target).__enter__().__class__
            ),
            "lock was not released after an exception",
        )

    def test_readers_are_concurrent(self):
        first_inside = threading.Event()
        second_inside = threading.Event()

        def reader(entered, other):
            with serialized_file_write(self.target, shared=True):
                entered.set()
                # Both readers must be inside at once for this to return True.
                other.wait(3.0)

        t1 = threading.Thread(
            target=reader, args=(first_inside, second_inside), daemon=True
        )
        t2 = threading.Thread(
            target=reader, args=(second_inside, first_inside), daemon=True
        )
        t1.start()
        t2.start()
        overlapped = first_inside.wait(3.0) and second_inside.wait(3.0)
        t1.join(5.0)
        t2.join(5.0)
        self.assertTrue(overlapped, "shared locks serialized instead of overlapping")

    def test_writer_excludes_a_reader(self):
        writer_inside = threading.Event()
        reader_entered = threading.Event()
        release_writer = threading.Event()

        def writer():
            with serialized_file_write(self.target):
                writer_inside.set()
                release_writer.wait(3.0)

        def reader():
            with serialized_file_write(self.target, shared=True):
                reader_entered.set()

        w = threading.Thread(target=writer, daemon=True)
        w.start()
        self.assertTrue(writer_inside.wait(3.0), "writer never acquired")

        r = threading.Thread(target=reader, daemon=True)
        r.start()
        # The reader must still be blocked while the writer holds the lane.
        self.assertFalse(
            reader_entered.wait(0.5), "reader entered while a writer held the lock"
        )

        release_writer.set()
        w.join(5.0)
        self.assertTrue(reader_entered.wait(3.0), "reader never acquired after release")
        r.join(5.0)

    def test_upgrading_a_shared_hold_to_exclusive_is_refused(self):
        # Proceeding would let a writer run while this thread holds only a read
        # lock, so concurrent readers could see a half-rewritten mask.
        with serialized_file_write(self.target, shared=True):
            with self.assertRaises(RuntimeError):
                with serialized_file_write(self.target):
                    pass

    def test_nested_shared_inside_shared_is_allowed(self):
        def nested():
            with serialized_file_write(self.target, shared=True):
                with serialized_file_write(self.target, shared=True):
                    pass

        self.assertTrue(self._run_guarded(nested), "shared-in-shared deadlocked")

    def test_root_creator_hands_lock_to_target_owner(self):
        self.target.touch()
        target_stat = self.target.stat()
        with (
            mock.patch("annotation.visualization.slice_io.os.geteuid", return_value=0),
            mock.patch("annotation.visualization.slice_io.os.fchown") as fchown,
            mock.patch("annotation.visualization.slice_io.os.fchmod") as fchmod,
        ):
            with serialized_file_write(self.target, shared=True):
                pass

        fd = fchown.call_args.args[0]
        fchown.assert_called_once_with(fd, target_stat.st_uid, target_stat.st_gid)
        fchmod.assert_called_once_with(fd, 0o664)
