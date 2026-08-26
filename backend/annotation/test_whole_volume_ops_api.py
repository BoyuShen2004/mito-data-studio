"""API-level no-autosave tests for whole-volume label plans.

``test_cellable_port.py`` already covers the split/merge *algorithms* on plain
arrays. These tests cover the part a unit test cannot: that going through the
HTTP endpoint returns an applicable before/after plan while leaving the
working volume and labels summary untouched until explicit Save.

That distinction matters because a successful plan that changes an mtime has
already bypassed pending Undo and violated the explicit-Save contract.

Every test runs against an isolated temporary ``MITO_DATA_ROOT`` and a source
image in its own tempdir, so nothing here can touch real microscopy data.
"""

from __future__ import annotations

import tempfile
import threading
from unittest import mock
from pathlib import Path

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from annotation.models import AnnotationTask
from core.choices import TaskType
from projects.models import Dataset, Project
from volumes.models import Volume

User = get_user_model()

# Big enough that each blob below clears ``split_components.SIZE_THRESHOLD``
# (100 voxels). Split deliberately deletes components smaller than that as
# noise, so an undersized fixture would test the discard path while looking
# like a broken split.
SHAPE = (6, 20, 20)
BLOB_VOXELS = 4 * 6 * 6  # 144, comfortably over the threshold


class WholeVolumeOpsApiTestCase(TestCase):
    """One task whose working copy holds two separate 3-D blobs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir(parents=True)

        # Source image lives outside the data root and is read-only input.
        self.external = Path(self.tmp.name) / "source"
        self.external.mkdir(parents=True)
        self.image = self.external / "cortex.tif"
        tifffile.imwrite(str(self.image), np.full(SHAPE, 180, dtype=np.uint8))

        self.user = User.objects.create_user(username="editor", password="x")
        self.project = Project.objects.create(title="Proj", created_by=self.user)
        self.dataset = Dataset.objects.create(project=self.project, name="DS")
        self.volume = Volume.objects.create(
            project=self.project,
            dataset=self.dataset,
            name="cortex",
            image_path=str(self.image),
        )
        self.task = AnnotationTask.objects.create(
            project=self.project,
            volume=self.volume,
            assigned_to=self.user,
            z_start=0,
            z_end=SHAPE[0],
            y_end=SHAPE[1],
            x_end=SHAPE[2],
            task_type=TaskType.MANUAL_ANNOTATION,
        )
        self.client.force_login(self.user)

    # --- helpers ---------------------------------------------------------

    def _working_path(self) -> Path:
        from annotation.label_paths import working_label_rel_path
        from annotation.visualization.slice_io import resolve_path

        return resolve_path(working_label_rel_path(self.volume))

    def _seed_working_copy(self, mask: np.ndarray) -> Path:
        """Write `mask` as the volume's working copy, through the real path."""
        from annotation.services import _save_label_volume
        from annotation.visualization import slice_io

        slice_io.clear_caches()
        _save_label_volume(self.volume, mask)
        return self._working_path()

    def _read_working(self) -> np.ndarray:
        from annotation.visualization.slice_io import read_label_array

        return np.asarray(read_label_array(self._working_path()))

    def _summary_labels(self) -> set[int]:
        """Label ids the labels-summary endpoint reports."""
        resp = self.client.get(f"/api/tasks/{self.task.pk}/labels-summary/")
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        data = resp.json()
        rows = data.get("labels", data if isinstance(data, list) else [])
        out = set()
        for row in rows:
            for key in ("label", "label_id", "id"):
                if key in row:
                    out.add(int(row[key]))
                    break
        return out

    @staticmethod
    def _apply_z_plan(before: np.ndarray, payload: dict) -> np.ndarray:
        from annotation.visualization.slice_io import decode_label_rle

        applied = before.copy()
        for item in payload.get("slices", []):
            applied[int(item["index"])] = decode_label_rle(
                item["runs"], tuple(item["shape"])
            )
        return applied

    @staticmethod
    def _two_blobs() -> np.ndarray:
        """Two spatially disconnected blobs, both labelled 5, plus a label 9.

        Each blob is `BLOB_VOXELS` voxels so Split keeps both instead of
        discarding them as sub-threshold noise, and they are separated on both
        y and x so they are genuinely disconnected in 3-D.
        """
        mask = np.zeros(SHAPE, dtype=np.int32)
        mask[0:4, 2:8, 2:8] = 5
        mask[0:4, 12:18, 12:18] = 5
        mask[0:4, 2:8, 12:18] = 9
        return mask


class SaveRejectsAnOutOfRangeSlice(WholeVolumeOpsApiTestCase):
    """A destructive whole-slice write must not be silently redirected.

    The index used to be clamped into range, so a stale index — or one
    belonging to a different axis after a switch — painted the caller's pixels
    onto the *last* slice of the axis and destroyed whatever was labelled
    there, on a slice the user never opened.
    """

    def _put(self, index: int, label: int = 3):
        h, w = SHAPE[1], SHAPE[2]
        return self.client.put(
            f"/api/tasks/{self.task.pk}/label-ids/",
            {
                "axis": "z",
                "index": index,
                "shape": [h, w],
                "runs": [[label, 5], [0, h * w - 5]],
                "origin": "manual",
            },
            content_type="application/json",
        )

    def test_an_index_past_the_end_is_rejected(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            before = self._read_working()
            resp = self._put(SHAPE[0] + 500)
            after = self._read_working()

        self.assertEqual(resp.status_code, 400, resp.content[:300])
        # Crucially, the last slice was NOT overwritten.
        np.testing.assert_array_equal(after, before)

    def test_a_negative_index_is_rejected(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            before = self._read_working()
            resp = self._put(-4)
            after = self._read_working()

        self.assertEqual(resp.status_code, 400, resp.content[:300])
        np.testing.assert_array_equal(after, before)

    def test_a_valid_index_still_writes(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            resp = self._put(SHAPE[0] - 1, label=21)
            after = self._read_working()

        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.assertIn(21, set(int(v) for v in np.unique(after[SHAPE[0] - 1])))

    def test_save_removes_metadata_after_the_last_voxel_is_deleted(self):
        from annotation.services import (
            _load_label_metadata_store,
            set_label_slice_ids,
        )
        from annotation.visualization.slice_io import encode_label_rle

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(np.zeros(SHAPE, dtype=np.int32))
            painted = np.zeros(SHAPE[1:], dtype=np.int32)
            painted[2:5, 2:5] = 7
            set_label_slice_ids(
                self.volume, "z", 1, list(painted.shape), encode_label_rle(painted)
            )
            store, _path = _load_label_metadata_store(self.volume)
            self.assertIn(7, store)

            empty = np.zeros_like(painted)
            set_label_slice_ids(
                self.volume, "z", 1, list(empty.shape), encode_label_rle(empty)
            )
            store, _path = _load_label_metadata_store(self.volume)

        self.assertNotIn(7, store)

    def test_stale_tab_gets_a_conflict_instead_of_overwriting_newer_work(self):
        from annotation.services import set_label_slice_ids
        from annotation.visualization.slice_io import encode_label_rle

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            stale = self.client.get(
                f"/api/tasks/{self.task.pk}/label-ids/?axis=z&index=0"
            ).json()
            newer = np.full(SHAPE[1:], 23, dtype=np.int32)
            set_label_slice_ids(
                self.volume, "z", 0, list(newer.shape), encode_label_rle(newer)
            )
            response = self.client.put(
                f"/api/tasks/{self.task.pk}/label-ids/",
                {
                    "axis": "z",
                    "index": 0,
                    "shape": list(newer.shape),
                    "runs": [[99, int(newer.size)]],
                    "expected_revision": stale["revision"],
                },
                content_type="application/json",
            )
            after = self._read_working()

        self.assertEqual(response.status_code, 409, response.content[:300])
        self.assertEqual(response.json()["reason"], "write_conflict")
        self.assertTrue(np.all(after[0] == 23))

    def test_concurrent_slice_writes_are_serialized_without_corruption(self):
        from annotation.services import set_label_slice_ids
        from annotation.visualization.slice_io import encode_label_rle

        failures = []
        barrier = threading.Barrier(2)

        def write(index, label):
            try:
                plane = np.full(SHAPE[1:], label, dtype=np.int32)
                barrier.wait()
                set_label_slice_ids(
                    self.volume,
                    "z",
                    index,
                    list(plane.shape),
                    encode_label_rle(plane),
                )
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(np.zeros(SHAPE, dtype=np.int32))
            threads = [
                threading.Thread(target=write, args=(0, 31)),
                threading.Thread(target=write, args=(1, 32)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            after = self._read_working()

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertTrue(np.all(after[0] == 31))
        self.assertTrue(np.all(after[1] == 32))


@override_settings(MITO_TRACKING_PROVIDER="local")
class SplitComponentsReturnsPendingPlan(WholeVolumeOpsApiTestCase):
    def test_split_mints_above_all_sidecar_reserved_ids(self):
        from annotation.services import (
            _load_label_metadata_store,
            _save_label_metadata_store,
        )

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            store, path = _load_label_metadata_store(self.volume)
            store.verify(10)
            _save_label_metadata_store(store, path)
            response = self.client.post(
                f"/api/tasks/{self.task.pk}/split-components/",
                {"label": 5},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertTrue(all(label_id > 10 for label_id in response.json()["new_label_ids"]))

    def test_split_refuses_a_verified_target_before_planning(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            verified = self.client.post(
                f"/api/tasks/{self.task.pk}/labels/5/lifecycle/",
                {"action": "verify"},
                content_type="application/json",
            )
            self.assertEqual(verified.status_code, 200, verified.content)
            before = self._read_working().copy()
            response = self.client.post(
                f"/api/tasks/{self.task.pk}/split-components/",
                {"label": 5},
                content_type="application/json",
            )
            after = self._read_working()

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["reason"], "verified_label_locked")
        self.assertIn("Unverify", response.json()["detail"])
        np.testing.assert_array_equal(after, before)

    def test_split_plan_relabels_without_touching_disk(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            path = self._seed_working_copy(self._two_blobs())
            before = self._read_working()
            before_mtime = path.stat().st_mtime_ns
            self.assertEqual(int((before == 5).sum()), 2 * BLOB_VOXELS)

            resp = self.client.post(
                f"/api/tasks/{self.task.pk}/split-components/",
                {"label": 5},
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200, resp.content[:300])
            after_disk = self._read_working()
            planned = self._apply_z_plan(before, resp.json())

        self.assertEqual(path.stat().st_mtime_ns, before_mtime)
        np.testing.assert_array_equal(after_disk, before)
        # One blob keeps id 5; the other became a new id.
        new_ids = set(int(v) for v in np.unique(planned)) - {0, 9}
        self.assertGreaterEqual(len(new_ids), 2, f"expected a split, got {new_ids}")
        # Total painted voxels are conserved — a split relabels, never deletes.
        self.assertEqual(int((planned > 0).sum()), int((before > 0).sum()))
        self.assertTrue(all(item.get("before_runs") for item in resp.json()["slices"]))

    def test_split_plan_does_not_change_summary_before_save(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            before_labels = self._summary_labels()

            resp = self.client.post(
                f"/api/tasks/{self.task.pk}/split-components/",
                {"label": 5},
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
            after_labels = self._summary_labels()

        self.assertEqual(after_labels, before_labels)

    def test_split_of_a_single_component_label_is_a_no_op_not_an_error(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            mask = np.zeros(SHAPE, dtype=np.int32)
            mask[0:4, 2:8, 2:8] = 7  # one connected, above-threshold blob
            self._seed_working_copy(mask)

            resp = self.client.post(
                f"/api/tasks/{self.task.pk}/split-components/",
                {"label": 7},
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200, resp.content[:300])
            after = self._read_working()

        self.assertEqual(set(int(v) for v in np.unique(after)) - {0}, {7})

    def test_split_of_a_nonexistent_label_is_rejected(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            resp = self.client.post(
                f"/api/tasks/{self.task.pk}/split-components/",
                {"label": 4242},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400, resp.content[:300])

    def test_split_rejects_an_oversized_crop_before_worker_memory_spikes(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            path = self._seed_working_copy(self._two_blobs())
            before = self._read_working()
            before_mtime = path.stat().st_mtime_ns
            with mock.patch.dict("os.environ", {"MITO_TOOL_PLAN_MAX_VOXELS": "10"}):
                resp = self.client.post(
                    f"/api/tasks/{self.task.pk}/split-components/",
                    {"label": 5},
                    content_type="application/json",
                )
            after = self._read_working()

        self.assertEqual(resp.status_code, 400, resp.content[:300])
        self.assertIn("bounded tool limit", resp.json()["detail"])
        self.assertEqual(path.stat().st_mtime_ns, before_mtime)
        np.testing.assert_array_equal(after, before)

    def test_split_never_touches_the_external_source_image(self):
        before = self.image.read_bytes()
        before_mtime = self.image.stat().st_mtime_ns
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            self.client.post(
                f"/api/tasks/{self.task.pk}/split-components/",
                {"label": 5},
                content_type="application/json",
            )
        self.assertEqual(self.image.read_bytes(), before)
        self.assertEqual(self.image.stat().st_mtime_ns, before_mtime)


class WatershedReturnsPendingPlan(WholeVolumeOpsApiTestCase):
    def _store_verified_orphan(self, label_id: int) -> None:
        from annotation.services import (
            _load_label_metadata_store,
            _save_label_metadata_store,
        )

        store, path = _load_label_metadata_store(self.volume)
        store.verify(label_id)
        _save_label_metadata_store(store, path)

    def test_watershed_never_mints_a_sidecar_verified_orphan_id(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            self._store_verified_orphan(1)

            response = self.client.post(
                f"/api/tasks/{self.task.pk}/watershed/",
                {
                    "label": 5,
                    "seeds": [
                        {"z": 1, "y": 3, "x": 3},
                        {"z": 1, "y": 6, "x": 6},
                    ],
                },
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertNotIn(1, response.json()["new_label_ids"])

    def test_watershed_plan_saves_then_new_and_unrelated_labels_verify(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            plan = self.client.post(
                f"/api/tasks/{self.task.pk}/watershed/",
                {
                    "label": 5,
                    "seeds": [
                        {"z": 1, "y": 3, "x": 3},
                        {"z": 1, "y": 6, "x": 6},
                    ],
                },
                content_type="application/json",
            )
            self.assertEqual(plan.status_code, 200, plan.content[:300])
            new_label = plan.json()["new_label_ids"][0]
            for item in plan.json()["slices"]:
                saved = self.client.put(
                    f"/api/tasks/{self.task.pk}/label-ids/",
                    {
                        "axis": plan.json()["axis"],
                        "index": item["index"],
                        "shape": item["shape"],
                        "runs": item["runs"],
                    },
                    content_type="application/json",
                )
                self.assertEqual(saved.status_code, 200, saved.content[:300])

            verified_new = self.client.post(
                f"/api/tasks/{self.task.pk}/labels/{new_label}/lifecycle/",
                {"action": "verify"},
                content_type="application/json",
            )
            verified_other = self.client.post(
                f"/api/tasks/{self.task.pk}/labels/9/lifecycle/",
                {"action": "verify"},
                content_type="application/json",
            )

        self.assertEqual(verified_new.status_code, 200, verified_new.content[:300])
        self.assertEqual(verified_other.status_code, 200, verified_other.content[:300])

    def test_labels_summary_exposes_a_sidecar_verified_orphan(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            self._store_verified_orphan(1)
            response = self.client.get(f"/api/tasks/{self.task.pk}/labels-summary/")

        self.assertEqual(response.status_code, 200, response.content[:300])
        row = next(item for item in response.json()["labels"] if item["id"] == 1)
        self.assertEqual(row["state"], "verified")
        self.assertEqual(row["voxel_count"], 0)
        self.assertEqual(response.json()["stats"]["verified"], 1)

    def test_watershed_rejects_a_plan_that_would_grow_a_verified_id(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            path = self._seed_working_copy(self._two_blobs())
            before = self._read_working().copy()
            self._store_verified_orphan(1)
            # Exercise the geometry guard independently of the reservation
            # guard by simulating a stale allocator view that omits id 1.
            with mock.patch(
                "annotation.services._reserved_label_ids", return_value={5, 9}
            ):
                response = self.client.post(
                    f"/api/tasks/{self.task.pk}/watershed/",
                    {
                        "label": 5,
                        "seeds": [
                            {"z": 1, "y": 3, "x": 3},
                            {"z": 1, "y": 6, "x": 6},
                        ],
                    },
                    content_type="application/json",
                )
            after = self._read_working()
            path_exists = path.exists()

        self.assertEqual(response.status_code, 409, response.content[:300])
        self.assertEqual(response.json()["reason"], "verified_label_locked")
        np.testing.assert_array_equal(after, before)
        self.assertTrue(path_exists)

    def test_reused_distant_label_id_uses_a_bounded_seed_local_crop(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            path = self._seed_working_copy(self._two_blobs())
            before = self._read_working()
            before_mtime = path.stat().st_mtime_ns
            with mock.patch.dict("os.environ", {"MITO_TOOL_PLAN_MAX_VOXELS": "1000"}):
                response = self.client.post(
                    f"/api/tasks/{self.task.pk}/watershed/",
                    {
                        "label": 5,
                        "seeds": [
                            {"z": 1, "y": 3, "x": 3},
                            {"z": 1, "y": 6, "x": 6},
                        ],
                    },
                    content_type="application/json",
                )
            after = self._read_working()
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertTrue(response.json()["slices"])
        self.assertEqual(response.json()["new_label_ids"], [1])
        self.assertEqual(path.stat().st_mtime_ns, before_mtime)
        np.testing.assert_array_equal(after, before)

    def test_seed_local_crop_still_refuses_a_truly_oversized_seed_span(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            with mock.patch.dict("os.environ", {"MITO_TOOL_PLAN_MAX_VOXELS": "1000"}):
                response = self.client.post(
                    f"/api/tasks/{self.task.pk}/watershed/",
                    {
                        "label": 5,
                        "seeds": [
                            {"z": 1, "y": 3, "x": 3},
                            {"z": 1, "y": 16, "x": 16},
                        ],
                    },
                    content_type="application/json",
                )
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIn("Z×Y×X", response.json()["detail"])


@override_settings(MITO_TRACKING_PROVIDER="local")
class MergeLabelsReturnsPendingPlan(WholeVolumeOpsApiTestCase):
    def test_merge_plan_removes_absorbed_label_without_touching_disk(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            path = self._seed_working_copy(self._two_blobs())
            before = self._read_working()
            before_mtime = path.stat().st_mtime_ns
            self.assertEqual(set(int(v) for v in np.unique(before)) - {0}, {5, 9})

            resp = self.client.post(
                f"/api/tasks/{self.task.pk}/merge-labels/",
                {"a": 5, "b": 9},
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200, resp.content[:300])
            after_disk = self._read_working()
            planned = self._apply_z_plan(before, resp.json())

        self.assertEqual(path.stat().st_mtime_ns, before_mtime)
        np.testing.assert_array_equal(after_disk, before)
        # Merge keeps the smaller id; 9 is gone entirely.
        self.assertEqual(set(int(v) for v in np.unique(planned)) - {0}, {5})
        # Voxels are moved, not discarded.
        self.assertEqual(int((planned == 5).sum()), int((before > 0).sum()))

    def test_merged_away_label_stays_in_summary_until_save(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            self.assertIn(9, self._summary_labels())

            resp = self.client.post(
                f"/api/tasks/{self.task.pk}/merge-labels/",
                {"a": 5, "b": 9},
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
            after_labels = self._summary_labels()

        self.assertIn(9, after_labels)
        self.assertIn(5, after_labels)

    def test_merge_keeps_the_smaller_id_regardless_of_argument_order(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            resp = self.client.post(
                f"/api/tasks/{self.task.pk}/merge-labels/",
                {"a": 9, "b": 5},  # reversed
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200, resp.content[:300])
            before = self._read_working()
            planned = self._apply_z_plan(before, resp.json())

        self.assertEqual(set(int(v) for v in np.unique(planned)) - {0}, {5})

    def test_merge_rejects_the_old_directed_body(self):
        """A stale client must fail loudly rather than merge the wrong way."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            resp = self.client.post(
                f"/api/tasks/{self.task.pk}/merge-labels/",
                {"source": 9, "target": 5},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)

    def test_merge_of_a_nonexistent_label_is_rejected(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            resp = self.client.post(
                f"/api/tasks/{self.task.pk}/merge-labels/",
                {"a": 5, "b": 4242},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400, resp.content[:300])

    def test_merge_does_not_promote_the_working_copy_to_the_official_label(self):
        """Whole-volume ops write the draft; approval is the only promotion."""
        official = self.external / "approved.tif"
        tifffile.imwrite(str(official), np.zeros(SHAPE, dtype=np.uint16))
        self.volume.label_path = str(official)
        self.volume.save(update_fields=["label_path"])
        before = official.read_bytes()

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            resp = self.client.post(
                f"/api/tasks/{self.task.pk}/merge-labels/",
                {"a": 5, "b": 9},
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)

        self.volume.refresh_from_db()
        self.assertEqual(self.volume.label_path, str(official))
        self.assertEqual(official.read_bytes(), before)

    def test_merge_requires_edit_permission(self):
        other = User.objects.create_user(username="stranger", password="x")
        self.client.force_login(other)
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed_working_copy(self._two_blobs())
            resp = self.client.post(
                f"/api/tasks/{self.task.pk}/merge-labels/",
                {"a": 5, "b": 9},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 403)
