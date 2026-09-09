"""The SAM 2 image-encoder LRU behind Annotate's mask tools."""

from __future__ import annotations

import contextlib
import time
import unittest.mock

import numpy as np
from django.test import SimpleTestCase, override_settings

from annotation.tracking.adapters import sam2_bridge


class FakePredictor:
    """Stands in for SAM2ImagePredictor's feature slot."""

    def __init__(self):
        self.encodes = 0
        self._features = None
        self._orig_hw = None
        self._is_image_set = False
        self._is_batch = False

    def set_image(self, rgb):
        self.encodes += 1
        # A distinct object per encode, so a test can tell which one was replayed.
        self._features = {
            "image_embed": _FakeTensor(self.encodes),
            "high_res_feats": [_FakeTensor(self.encodes)],
        }
        self._orig_hw = [rgb.shape[:2]]
        self._is_image_set = True


class _FakeTensor:
    def __init__(self, tag, size=4, count=1024):
        self.tag = tag
        self._size = size
        self._count = count

    def element_size(self):
        return self._size

    def nelement(self):
        return self._count


class FakeStore:
    """An in-memory stand-in for the on-disk L2."""

    def __init__(self):
        self.files = {}
        self.saves = 0
        self.locks = 0

    def has(self, key):
        return key in self.files

    def load(self, key):
        return self.files.get(key)

    def save(self, key, payload):
        self.saves += 1
        self.files[key] = payload

    @contextlib.contextmanager
    def compute_lock(self, key):
        self.locks += 1
        yield


def _wrapper(predictor, slots=3, store=None):
    """A SAM2Wrapper with its cache fields, without loading any model."""
    w = sam2_bridge.SAM2Wrapper.__new__(sam2_bridge.SAM2Wrapper)
    import collections
    import threading

    w._image_predictor = predictor
    w._image_cache = collections.OrderedDict()
    w._image_cache_slots = slots
    w._image_cache_stats = {
        "hit": 0, "disk_hit": 0, "miss": 0, "uncached": 0, "evicted": 0
    }
    w._feature_store = store
    w._image_lock = threading.RLock()
    w.device = "cpu"
    return w


SLICE = np.zeros((8, 8), dtype=np.uint8)


class ImageCacheTests(SimpleTestCase):
    def test_a_repeat_slice_replays_features_instead_of_encoding(self):
        p = FakePredictor()
        w = _wrapper(p)

        self.assertEqual(w.encode_slice(SLICE, cache_key="z1"), "miss")
        self.assertEqual(w.encode_slice(SLICE, cache_key="z1"), "hit")
        self.assertEqual(w.encode_slice(SLICE, cache_key="z1"), "hit")

        self.assertEqual(p.encodes, 1)

    def test_scrubbing_back_within_the_window_is_a_hit(self):
        p = FakePredictor()
        w = _wrapper(p, slots=3)
        for key in ("z1", "z2", "z3"):
            w.encode_slice(SLICE, cache_key=key)
        self.assertEqual(p.encodes, 3)

        # Walk back over the same planes: no further encoding at all.
        for key in ("z3", "z2", "z1"):
            self.assertEqual(w.encode_slice(SLICE, cache_key=key), "hit")
        self.assertEqual(p.encodes, 3)

    def test_a_hit_restores_that_slice_and_not_the_last_encoded_one(self):
        p = FakePredictor()
        w = _wrapper(p)
        w.encode_slice(SLICE, cache_key="z1")   # encode 1
        w.encode_slice(SLICE, cache_key="z2")   # encode 2

        w.encode_slice(SLICE, cache_key="z1")

        self.assertEqual(p._features["image_embed"].tag, 1)
        self.assertTrue(p._is_image_set)
        self.assertFalse(p._is_batch)

    def test_eviction_is_least_recently_used_not_least_recently_added(self):
        p = FakePredictor()
        w = _wrapper(p, slots=2)
        w.encode_slice(SLICE, cache_key="z1")
        w.encode_slice(SLICE, cache_key="z2")
        w.encode_slice(SLICE, cache_key="z1")   # z1 is now the freshest
        w.encode_slice(SLICE, cache_key="z3")   # evicts z2, not z1

        self.assertEqual(w.encode_slice(SLICE, cache_key="z1"), "hit")
        self.assertEqual(w.encode_slice(SLICE, cache_key="z2"), "miss")

    def test_the_cache_never_grows_past_its_slots(self):
        p = FakePredictor()
        w = _wrapper(p, slots=3)
        for z in range(20):
            w.encode_slice(SLICE, cache_key=f"z{z}")

        stats = w.image_cache_stats()
        self.assertEqual(stats["held"], 3)
        self.assertEqual(stats["evicted"], 17)

    def test_a_slice_with_no_identity_is_encoded_but_never_cached(self):
        # Two different images both arriving with no key must not be served
        # each other's features.
        p = FakePredictor()
        w = _wrapper(p)

        w._ensure_image_features(p, SLICE, None)
        w._ensure_image_features(p, SLICE, None)

        self.assertEqual(p.encodes, 2)
        self.assertEqual(w.image_cache_stats()["held"], 0)
        self.assertEqual(w.image_cache_stats()["uncached"], 2)

    def test_reported_bytes_track_what_is_held(self):
        p = FakePredictor()
        w = _wrapper(p, slots=3)
        self.assertEqual(w.image_cache_bytes(), 0)
        w.encode_slice(SLICE, cache_key="z1")
        # image_embed + one high_res_feats entry, 4 bytes x 1024 each.
        self.assertEqual(w.image_cache_bytes(), 2 * 4 * 1024)

    def test_reset_clears_features_and_counters(self):
        p = FakePredictor()
        w = _wrapper(p)
        w.encode_slice(SLICE, cache_key="z1")
        w.reset_image_cache()

        self.assertEqual(w.image_cache_stats()["held"], 0)
        self.assertEqual(w.image_cache_stats()["miss"], 0)
        self.assertEqual(w.encode_slice(SLICE, cache_key="z1"), "miss")

    def test_a_prompt_encodes_then_decodes_under_one_lock(self):
        p = FakePredictor()
        w = _wrapper(p)
        p.predict = unittest.mock.Mock(
            return_value=(np.ones((1, 8, 8), dtype=np.float32), np.array([0.9]), None)
        )
        w._slice_to_rgb = staticmethod(lambda s: np.zeros((8, 8, 3), dtype=np.uint8))

        with unittest.mock.patch.object(
            sam2_bridge.SAM2Wrapper, "_get_image_predictor", return_value=p
        ):
            out = sam2_bridge.SAM2Wrapper.predict_single_frame(
                w, SLICE, points=[(1, 1)], point_labels=[1], cache_key="z1"
            )
            sam2_bridge.SAM2Wrapper.predict_single_frame(
                w, SLICE, points=[(2, 2)], point_labels=[1], cache_key="z1"
            )

        self.assertEqual(out.dtype, np.dtype(bool))
        self.assertEqual(p.encodes, 1)          # second prompt reused the slice
        self.assertEqual(p.predict.call_count, 2)


class CacheSlotSettingTests(SimpleTestCase):
    @override_settings(MITO_SAM2_IMAGE_CACHE_SLOTS=5)
    def test_the_slot_count_comes_from_settings(self):
        self.assertEqual(sam2_bridge._setting_int("MITO_SAM2_IMAGE_CACHE_SLOTS", 8), 5)

    def test_a_missing_setting_falls_back_to_the_default(self):
        self.assertEqual(sam2_bridge._setting_int("MITO_NOT_A_SETTING", 8), 8)


class DiskBackedCacheTests(SimpleTestCase):
    """L1 is per worker; the L2 is what stops a sibling re-encoding."""

    def test_a_miss_encodes_once_and_writes_the_features_out(self):
        store = FakeStore()
        p = FakePredictor()
        w = _wrapper(p, store=store)
        w._export = staticmethod(lambda f, hw: {"features": f, "orig_hw": hw})

        self.assertEqual(w.encode_slice(SLICE, cache_key="z1"), "miss")

        self.assertEqual(p.encodes, 1)
        self.assertEqual(store.saves, 1)
        self.assertIn("z1", store.files)

    def test_a_cold_worker_reads_the_slice_instead_of_re_encoding_it(self):
        # The bug this exists for: worker A warmed z1, the annotator's next
        # request lands on worker B, and B used to pay a full encode.
        store = FakeStore()
        a = _wrapper(FakePredictor(), store=store)
        a._export = staticmethod(lambda f, hw: {"features": f, "orig_hw": hw})
        a.encode_slice(SLICE, cache_key="z1")

        worker_b = FakePredictor()
        b = _wrapper(worker_b, store=store)
        b._import = lambda payload: (payload["features"], payload["orig_hw"])

        self.assertEqual(b.encode_slice(SLICE, cache_key="z1"), "disk_hit")
        self.assertEqual(worker_b.encodes, 0)

    def test_a_disk_hit_populates_the_local_lru_so_the_next_click_is_free(self):
        store = FakeStore()
        a = _wrapper(FakePredictor(), store=store)
        a._export = staticmethod(lambda f, hw: {"features": f, "orig_hw": hw})
        a.encode_slice(SLICE, cache_key="z1")

        p = FakePredictor()
        b = _wrapper(p, store=store)
        b._import = lambda payload: (payload["features"], payload["orig_hw"])

        self.assertEqual(b.encode_slice(SLICE, cache_key="z1"), "disk_hit")
        self.assertEqual(b.encode_slice(SLICE, cache_key="z1"), "hit")
        self.assertEqual(p.encodes, 0)

    def test_the_encode_happens_under_the_cross_process_lock(self):
        store = FakeStore()
        w = _wrapper(FakePredictor(), store=store)
        w._export = staticmethod(lambda f, hw: {"features": f, "orig_hw": hw})

        w.encode_slice(SLICE, cache_key="z1")

        self.assertEqual(store.locks, 1)

    def test_a_slice_written_while_we_waited_for_the_lock_is_not_re_encoded(self):
        # Three warms for the same slice arrive together; only the first
        # should encode. The others must re-check inside the lock.
        store = FakeStore()
        p = FakePredictor()
        w = _wrapper(p, store=store)
        w._import = lambda payload: (payload["features"], payload["orig_hw"])

        @contextlib.contextmanager
        def racing_lock(key):
            store.files[key] = {"features": "from-a-sibling", "orig_hw": [(8, 8)]}
            yield

        store.compute_lock = racing_lock

        self.assertEqual(w.encode_slice(SLICE, cache_key="z1"), "disk_hit")
        self.assertEqual(p.encodes, 0)
        self.assertEqual(p._features, "from-a-sibling")

    def test_without_a_store_the_lru_still_works_on_its_own(self):
        p = FakePredictor()
        w = _wrapper(p, store=None)

        self.assertEqual(w.encode_slice(SLICE, cache_key="z1"), "miss")
        self.assertEqual(w.encode_slice(SLICE, cache_key="z1"), "hit")
        self.assertEqual(p.encodes, 1)

    def test_an_unkeyed_slice_never_touches_the_store(self):
        store = FakeStore()
        p = FakePredictor()
        w = _wrapper(p, store=store)

        w._ensure_image_features(p, SLICE, None)

        self.assertEqual(store.saves, 0)
        self.assertEqual(store.files, {})
        self.assertEqual(p.encodes, 1)


class OpportunisticWarmTests(SimpleTestCase):
    """Warming must never make the requests it is prefetching for wait."""

    def test_a_slice_already_cached_locally_does_no_work(self):
        p = FakePredictor()
        w = _wrapper(p)
        w.encode_slice(SLICE, cache_key="z1")

        self.assertEqual(w.warm_slice(SLICE, cache_key="z1"), "hit")
        self.assertEqual(p.encodes, 1)

    def test_a_slice_a_sibling_worker_cached_does_no_work_either(self):
        # Existence on disk is enough: the click path loads the bytes, so a
        # warm must not spend 8 MiB of IO to rediscover that.
        store = FakeStore()
        store.files["z1"] = {"features": "sibling", "orig_hw": [(8, 8)]}
        p = FakePredictor()
        w = _wrapper(p, store=store)

        self.assertEqual(w.warm_slice(SLICE, cache_key="z1"), "hit")
        self.assertEqual(p.encodes, 0)

    def test_warming_skips_rather_than_queueing_when_the_gpu_is_busy(self):
        # This is the scrubbing bug: three warms per slice change used to sit
        # on gunicorn threads waiting for the encode lock while the slice and
        # label requests the annotator was waiting for queued behind them.
        import threading

        p = FakePredictor()
        w = _wrapper(p)
        held, release = threading.Event(), threading.Event()

        def hold():  # stands in for another request mid-encode
            with w._image_lock:
                held.set()
                release.wait(2)

        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(2)
        started = time.perf_counter()
        out = w.warm_slice(SLICE, cache_key="z1")
        elapsed = time.perf_counter() - started
        release.set()
        holder.join(2)

        self.assertEqual(out, "busy")
        self.assertEqual(p.encodes, 0)
        # The point is the *thread* comes back immediately, not eventually.
        self.assertLess(elapsed, 0.5, "warm waited for the encode lock")

    def test_a_skipped_warm_is_encoded_later_rather_than_lost(self):
        import threading

        p = FakePredictor()
        w = _wrapper(p)
        # The lock has to be held by *another* thread to contend: it is an
        # RLock, so a same-thread non-blocking acquire simply succeeds.
        held, release = threading.Event(), threading.Event()

        def hold():
            with w._image_lock:
                held.set()
                release.wait(2)

        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(2)
        skipped = w.warm_slice(SLICE, cache_key="z1")
        release.set()
        holder.join(2)

        self.assertEqual(skipped, "busy")
        self.assertEqual(p.encodes, 0)
        # Nothing was lost — the next warm (or the click) still encodes it.
        self.assertEqual(w.warm_slice(SLICE, cache_key="z1"), "miss")
        self.assertEqual(p.encodes, 1)

    def test_is_slice_warm_reports_both_cache_levels(self):
        store = FakeStore()
        p = FakePredictor()
        w = _wrapper(p, store=store)

        self.assertFalse(w.is_slice_warm("z1"))
        self.assertFalse(w.is_slice_warm(None))
        store.files["z1"] = {"features": "x", "orig_hw": [(8, 8)]}
        self.assertTrue(w.is_slice_warm("z1"))
