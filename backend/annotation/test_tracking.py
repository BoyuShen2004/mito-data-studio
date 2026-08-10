"""Tests for fork-aware SAM2 tracking + slice IO + role gating."""

import os
import tempfile
from unittest import mock

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import AnnotatorProfile, UserProfile
from annotation.label_paths import working_label_rel_path
from annotation.models import AnnotationTask
from annotation.tracking.branching import (
    TrackGroup,
    merge_group,
    split_binary_mask_components,
)
from annotation.tracking.services import run_branch_tracking
from annotation.tracking.interfaces import TrackingProvider
from annotation.visualization import slice_io
from core.choices import LabelType, TaskStatus, TaskType, UserRole
from projects.services import create_project
from volumes.models import Volume

User = get_user_model()
_TMP = tempfile.mkdtemp(prefix="mito-track-test-")


class BranchingUnitTests(TestCase):
    def test_split_components_finds_forks(self):
        m = np.zeros((10, 10), dtype=bool)
        m[1:3, 1:3] = True  # blob A
        m[6:9, 6:9] = True  # blob B (disconnected)
        comps = split_binary_mask_components(m)
        self.assertEqual(len(comps), 2)

    def test_merge_group_collapses_branches(self):
        vol = np.zeros((3, 4, 4), dtype=np.int32)
        vol[0, 0, 0] = 5   # final id
        vol[1, 1, 1] = 7   # branch
        vol[2, 2, 2] = 9   # branch
        group = TrackGroup(group_id=5, branch_ids=[5, 7, 9])
        merge_group(vol, group)
        self.assertEqual(set(np.unique(vol)) - {0}, {5})


class TrackingProviderRegistryTests(TestCase):
    @override_settings(MITO_TRACKING_PROVIDER="local")
    def test_provider_is_reused_within_process(self):
        from annotation.tracking.registry import (
            get_tracking_provider,
            reset_tracking_providers,
        )

        reset_tracking_providers()
        try:
            self.assertIs(get_tracking_provider(), get_tracking_provider())
        finally:
            reset_tracking_providers()


class Sam2AutocastContractTests(TestCase):
    """SAM 2 stores its memory bank in bfloat16 unconditionally, so every CUDA
    code path must run under autocast — bare fp32 makes memory attention die
    with ``mat1 and mat2 must have the same dtype``. That only bites once a
    parent has two or more child classes (the multi-object consolidation path
    is what first pushes bf16 memory through ``memory_attention``), which is
    why single-seed Track looked healthy while every real fork propagate
    failed on this deployment's sm_75 nodes.

    Asserted on the context manager rather than a live model so it runs in CI
    without a GPU or the 2.4 GB checkpoint.
    """

    def _wrapper(self, device, autocast_dtype):
        from annotation.tracking.adapters.sam2_bridge import SAM2Wrapper

        wrapper = SAM2Wrapper.__new__(SAM2Wrapper)
        wrapper.device = device
        wrapper._autocast_dtype = autocast_dtype
        return wrapper

    def test_cuda_inference_always_enables_autocast(self):
        torch = self._torch()
        for dtype in (torch.bfloat16, torch.float16):
            wrapper = self._wrapper("cuda", dtype)
            with wrapper._inference_context():
                self.assertTrue(
                    torch.is_autocast_enabled(),
                    f"autocast must be active for {dtype} — without it SAM 2's "
                    "bf16 memory bank cannot match fp32 module weights",
                )
                self.assertEqual(torch.get_autocast_dtype("cuda"), dtype)

    def test_cpu_inference_runs_without_autocast(self):
        torch = self._torch()
        wrapper = self._wrapper("cpu", None)
        with wrapper._inference_context():
            self.assertFalse(torch.is_autocast_enabled())

    def _torch(self):
        try:
            import torch
        except ImportError:  # pragma: no cover - torch-free CI
            self.skipTest("torch is not installed")
        return torch


@override_settings(MITO_TRACKING_PROVIDER="local")
class ForkTrackingServiceTests(TestCase):
    def test_explicit_user_subclasses_across_slices_merge_to_parent(self):
        image = np.full((5, 12, 12), 200, dtype=np.uint8)
        volume_mask = np.zeros((5, 12, 12), dtype=np.int32)
        first = np.zeros((12, 12), dtype=bool)
        second = np.zeros((12, 12), dtype=bool)
        third = np.zeros((12, 12), dtype=bool)
        first[1:3, 1:3] = True
        second[8:10, 8:10] = True
        third[1:3, 8:10] = True

        result = run_branch_tracking(
            image=image,
            volume_mask=volume_mask,
            seeds={},
            branch_seeds={1: {1: first}, 2: {3: second}, 3: {4: third}},
            z_range=(0, 4),
            group_id=17,
        )

        self.assertEqual(result["final_id"], 17)
        self.assertEqual(result["group"]["seed_zs"], [1, 3, 4])
        self.assertEqual(
            set(result["group"]["subclass_branch_ids"]), {"1", "2", "3"}
        )
        self.assertEqual(set(np.unique(volume_mask)) - {0}, {17})
        # Local subclass 2 is not a permanent label id.
        self.assertFalse(np.any(volume_mask == 2))

    def test_tracking_does_not_overwrite_an_unrelated_parent(self):
        image = np.full((3, 8, 8), 150, dtype=np.uint8)
        volume_mask = np.zeros((3, 8, 8), dtype=np.int32)
        volume_mask[:, 3, 3] = 22
        seed = np.zeros((8, 8), dtype=bool)
        seed[2:5, 2:5] = True
        run_branch_tracking(
            image=image,
            volume_mask=volume_mask,
            seeds={},
            branch_seeds={1: {1: seed}},
            z_range=(0, 2),
            group_id=17,
        )
        self.assertTrue(np.all(volume_mask[:, 3, 3] == 22))

    def test_overwrite_all_replaces_an_unrelated_parent(self):
        image = np.full((3, 8, 8), 150, dtype=np.uint8)
        volume_mask = np.zeros((3, 8, 8), dtype=np.int32)
        volume_mask[:, 3, 3] = 22
        seed = np.zeros((8, 8), dtype=bool)
        seed[2:5, 2:5] = True
        run_branch_tracking(
            image=image,
            volume_mask=volume_mask,
            seeds={},
            branch_seeds={1: {1: seed}},
            z_range=(0, 2),
            group_id=17,
            protect_other_labels=False,
        )
        self.assertTrue(np.all(volume_mask[:, 3, 3] == 17))

    def test_provider_failure_leaves_mask_unchanged(self):
        class FailingProvider(TrackingProvider):
            def propagate(self, request):
                raise RuntimeError("provider failed")

        image = np.zeros((3, 8, 8), dtype=np.uint8)
        volume_mask = np.zeros((3, 8, 8), dtype=np.int32)
        volume_mask[0, 0, 0] = 9
        before = volume_mask.copy()
        seed = np.zeros((8, 8), dtype=bool)
        seed[1:3, 1:3] = True
        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            run_branch_tracking(
                image=image,
                volume_mask=volume_mask,
                seeds={},
                branch_seeds={1: {1: seed}},
                provider=FailingProvider(),
                group_id=17,
            )
        np.testing.assert_array_equal(volume_mask, before)

    def test_fork_seeds_branch_ids_then_merges_to_one(self):
        # A bright volume so the local provider carries the seed everywhere.
        image = np.full((5, 12, 12), 200, dtype=np.uint8)
        volume_mask = np.zeros((5, 12, 12), dtype=np.int32)

        seed = np.zeros((12, 12), dtype=bool)
        seed[1:3, 1:3] = True    # branch 1
        seed[8:10, 8:10] = True  # branch 2 (fork!)

        result = run_branch_tracking(
            image=image,
            volume_mask=volume_mask,
            seeds={2: seed},
            z_range=(0, 4),
        )

        # Two temporary branch ids were used during tracking...
        self.assertEqual(len(result["branch_ids"]), 2)
        self.assertEqual(len(set(result["branch_ids"])), 2)
        # ...but the persisted mask holds exactly one final instance id.
        labels = set(int(v) for v in np.unique(volume_mask)) - {0}
        self.assertEqual(labels, {result["final_id"]})
        # Group metadata records the branch → final mapping for audit / re-run.
        self.assertEqual(result["group"]["final_id"], result["final_id"])
        self.assertCountEqual(
            result["group"]["branch_ids"], result["branch_ids"]
        )

    def test_single_component_seed_needs_no_merge(self):
        image = np.full((3, 8, 8), 150, dtype=np.uint8)
        volume_mask = np.zeros((3, 8, 8), dtype=np.int32)
        seed = np.zeros((8, 8), dtype=bool)
        seed[2:5, 2:5] = True
        result = run_branch_tracking(
            image=image, volume_mask=volume_mask, seeds={1: seed}, z_range=(0, 2)
        )
        self.assertEqual(len(result["branch_ids"]), 1)
        self.assertEqual(result["final_id"], result["branch_ids"][0])


@override_settings(MITO_DATA_ROOT=_TMP, MEDIA_ROOT=_TMP)
class SliceIOTests(TestCase):
    def setUp(self):
        slice_io.clear_caches()
        self.rel = "images/vol.tif"
        path = os.path.join(_TMP, self.rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # z ramps so slices are distinguishable.
        data = np.stack(
            [np.full((16, 24), i * 10, dtype=np.uint8) for i in range(20)]
        )
        tifffile.imwrite(path, data)

    def test_meta_and_three_axis_slices(self):
        meta = slice_io.volume_meta(self.rel)
        self.assertEqual(meta["shape"], {"z": 20, "y": 16, "x": 24})
        self.assertEqual(slice_io.read_slice(self.rel, "z", 3).shape, (16, 24))
        self.assertEqual(slice_io.read_slice(self.rel, "y", 3).shape, (20, 24))
        self.assertEqual(slice_io.read_slice(self.rel, "x", 3).shape, (20, 16))

    def test_slice_cache_is_bounded(self):
        original = slice_io.MAX_SLICE_CACHE
        slice_io.MAX_SLICE_CACHE = 4
        try:
            for i in range(20):
                slice_io.read_slice(self.rel, "z", i % 20)
            self.assertLessEqual(slice_io.cache_stats()["slices"], 4)
        finally:
            slice_io.MAX_SLICE_CACHE = original

    def test_png_encoding_roundtrips_dimensions(self):
        png = slice_io.render_image_slice_png(self.rel, "z", 5, window=255, level=128)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_invalidating_one_file_keeps_other_files_cached(self):
        """A label write must not evict every other volume's warm slices —
        that turned one annotator's Save into a cold cache for everyone
        else (progress/history/03-fix-hard-case-share-view.md item C)."""
        other_rel = "images/other.tif"
        other_path = os.path.join(_TMP, other_rel)
        tifffile.imwrite(other_path, np.zeros((4, 8, 8), dtype=np.uint8))

        slice_io.read_slice(self.rel, "z", 1)
        slice_io.render_image_slice_jpeg(self.rel, "z", 1)
        slice_io.read_slice(other_rel, "z", 1)
        slice_io.render_image_slice_jpeg(other_rel, "z", 1)
        before = slice_io.cache_stats()

        slice_io.invalidate_read_caches(slice_io.resolve_path(self.rel))
        after = slice_io.cache_stats()
        # Exactly the written file's entries went; the other file's stayed.
        self.assertEqual(after["slices"], before["slices"] - 1)
        self.assertEqual(after["encoded"], before["encoded"] - 1)
        self.assertIsNotNone(
            slice_io._lru_get(
                slice_io._slice_cache,
                (
                    str(slice_io.resolve_path(other_rel)),
                    slice_io.resolve_path(other_rel).stat().st_mtime,
                    "z",
                    1,
                ),
            )
        )

    def test_drop_file_releases_only_that_file(self):
        slice_io.read_slice(self.rel, "z", 2)
        slice_io.volume_meta(self.rel)
        self.assertGreater(slice_io.cache_stats()["volumes"], 0)
        slice_io.drop_file(slice_io.resolve_path(self.rel))
        self.assertEqual(slice_io.cache_stats()["volumes"], 0)
        self.assertEqual(slice_io.cache_stats()["slices"], 0)


@override_settings(
    MITO_DATA_ROOT=_TMP,
    MEDIA_ROOT=_TMP,
    MITO_TRACKING_PROVIDER="local",
)
class RoleGatingApiTests(TestCase):
    def setUp(self):
        slice_io.clear_caches()
        self.manager = self._user("mgr", UserRole.MANAGER)
        self.annotator = self._user("ann", UserRole.ANNOTATOR, annotator=True)
        self.requester = self._user("req", UserRole.REQUESTER)

        self.project = create_project(
            title="P", created_by=self.requester, reviewed=True
        )
        rel = "images/task.tif"
        path = os.path.join(_TMP, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tifffile.imwrite(path, np.full((6, 10, 10), 200, dtype=np.uint8))
        self.volume = Volume.objects.create(
            project=self.project, name="v", image_path=rel,
            label_type=LabelType.NONE, shape_z=6, shape_y=10, shape_x=10,
        )
        # Django's per-test transaction rollback resets the DB but not the
        # filesystem: SQLite reuses rowids after a rollback, so a later
        # test's volume can get the same id as an earlier test's and find its
        # leftover owned working-copy file still on disk in the shared _TMP
        # dir. Clear it so each test starts fresh.
        owned = os.path.join(_TMP, working_label_rel_path(self.volume))
        if os.path.exists(owned):
            os.remove(owned)
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume, assigned_to=self.annotator,
            z_start=0, z_end=6, y_end=10, x_end=10,
            task_type=TaskType.MANUAL_ANNOTATION,
            # `assigned_to` alone leaves `status` at its UNASSIGNED default, so
            # the submit-and-approve tests below drove an unassigned task
            # straight to SUBMITTED and tripped `assert_transition`. Every other
            # fixture in the suite pairs the two (test_operations,
            # test_review_loop, test_submit_loop, ...); this one had drifted.
            status=TaskStatus.ASSIGNED,
        )

    def _user(self, name, role, annotator=False):
        user = User.objects.create_user(name, password="x")
        # A post_save signal already made a default profile and cached it on
        # ``user``; update the row and re-fetch so the role isn't read stale.
        UserProfile.objects.filter(user=user).update(role=role)
        if annotator:
            AnnotatorProfile.objects.create(user=user, is_active_annotator=True)
        return User.objects.get(pk=user.pk)

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def _seed_payload(self):
        # A 3x3 blob at the top-left, RLE over the flattened 10x10 seed slice.
        rle = [[0, 3], [10, 3], [20, 3]]
        return {"seeds": [{"z": 2, "rle": rle, "shape": [10, 10]}]}

    def _prompt_payload(self, parent_id, x_offset=0):
        rle = [[x_offset, 2], [10 + x_offset, 2]]
        return {
            "parent_id": parent_id,
            "subclasses": [
                {"index": 1, "seeds": [{"z": 2, "rle": rle, "shape": [10, 10]}]},
                {"index": 2, "seeds": [{"z": 3, "rle": [[80 + x_offset, 2]], "shape": [10, 10]}]},
            ],
            "z_range": [0, 5],
            "status": "ready",
            "note": "fork",
        }

    def test_prompt_queue_is_unbounded_and_batch_merges_subclasses(self):
        client = self._client(self.annotator)
        for parent_id in range(17, 29):
            response = client.put(
                f"/api/tasks/{self.task.id}/track/prompts/",
                self._prompt_payload(parent_id, (parent_id - 17) % 7),
                format="json",
            )
            self.assertEqual(response.status_code, 200, response.content)
        queue = client.get(f"/api/tasks/{self.task.id}/track/prompts/")
        self.assertEqual(queue.status_code, 200, queue.content)
        self.assertEqual(len(queue.json()["items"]), 12)
        self.assertTrue(all(item["z_range"] == [2, 3] for item in queue.json()["items"]))

        response = client.post(
            f"/api/tasks/{self.task.id}/track/batch/",
            {"parent_ids": [17, 18]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["done"], 2)
        self.assertTrue(response.json()["slices"])
        self.volume.refresh_from_db()
        self.assertFalse(slice_io.resolve_path(working_label_rel_path(self.volume)).exists())
        statuses = {
            item["parent_id"]: item["status"]
            for item in self.volume.metadata["tracking_prompts"]["items"]
        }
        self.assertEqual(statuses[17], "ready")
        self.assertEqual(statuses[18], "ready")
        self.assertEqual(statuses[19], "ready")
        self.assertNotIn("tracking_pending_review", self.volume.metadata)

    def test_propagating_selected_parent_keeps_sibling_prompt_and_seeds(self):
        client = self._client(self.annotator)
        parent_a = self._prompt_payload(17, 0)
        parent_b = self._prompt_payload(18, 5)
        for payload in (parent_a, parent_b):
            response = client.put(
                f"/api/tasks/{self.task.id}/track/prompts/",
                payload,
                format="json",
            )
            self.assertEqual(response.status_code, 200, response.content)

        propagated = client.post(
            f"/api/tasks/{self.task.id}/track/batch/",
            {"parent_ids": [17]},
            format="json",
        )
        self.assertEqual(propagated.status_code, 200, propagated.content)
        self.assertEqual(propagated.json()["done"], 1)

        queue = client.get(f"/api/tasks/{self.task.id}/track/prompts/")
        self.assertEqual(queue.status_code, 200, queue.content)
        by_parent = {item["parent_id"]: item for item in queue.json()["items"]}
        self.assertEqual(set(by_parent), {17, 18})
        self.assertEqual(by_parent[17]["status"], "ready")
        self.assertEqual(by_parent[18]["status"], "ready")
        self.assertEqual(by_parent[18]["subclasses"], parent_b["subclasses"])

    def _working_mask(self):
        self.volume.refresh_from_db()
        return np.asarray(
            tifffile.imread(slice_io.resolve_path(working_label_rel_path(self.volume)))
        )

    def test_propagate_returns_pending_plan_without_server_preview(self):
        client = self._client(self.annotator)
        response = client.put(
            f"/api/tasks/{self.task.id}/track/prompts/",
            self._prompt_payload(17),
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        propagated = client.post(
            f"/api/tasks/{self.task.id}/track/batch/",
            {"parent_ids": [17]},
            format="json",
        )
        self.assertEqual(propagated.status_code, 200, propagated.content)
        self.assertTrue(propagated.json()["slices"])
        self.assertTrue(
            all(item.get("before_runs") for item in propagated.json()["slices"])
        )

        self.volume.refresh_from_db()
        self.assertEqual(self.volume.metadata.get("tracking_groups", []), [])
        self.assertNotIn("tracking_pending_review", self.volume.metadata)
        self.assertFalse(slice_io.resolve_path(working_label_rel_path(self.volume)).exists())
        queue = client.get(f"/api/tasks/{self.task.id}/track/prompts/")
        self.assertIsNone(queue.json()["pending_review"])

        # Planning does not lock durable prompt editing.
        editable = client.put(
            f"/api/tasks/{self.task.id}/track/prompts/",
            self._prompt_payload(19),
            format="json",
        )
        self.assertEqual(editable.status_code, 200)

    def test_repeated_track_plans_never_create_a_working_mask(self):
        client = self._client(self.annotator)
        for parent_id in (17, 18):
            response = client.put(
                f"/api/tasks/{self.task.id}/track/prompts/",
                self._prompt_payload(parent_id, 0 if parent_id == 17 else 5),
                format="json",
            )
            self.assertEqual(response.status_code, 200, response.content)

        for parent_id in (17, 18):
            planned = client.post(
                f"/api/tasks/{self.task.id}/track/batch/",
                {"parent_ids": [parent_id]},
                format="json",
            )
            self.assertEqual(planned.status_code, 200, planned.content)
            self.assertTrue(planned.json()["slices"])
        self.assertFalse(slice_io.resolve_path(working_label_rel_path(self.volume)).exists())

    def test_track_slab_limit_fails_before_allocating_or_writing(self):
        client = self._client(self.annotator)
        response = client.put(
            f"/api/tasks/{self.task.id}/track/prompts/",
            self._prompt_payload(17),
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        with mock.patch.dict("os.environ", {"MITO_TRACK_PLAN_MAX_VOXELS": "100"}):
            planned = client.post(
                f"/api/tasks/{self.task.id}/track/batch/",
                {"parent_ids": [17]},
                format="json",
            )

        self.assertEqual(planned.status_code, 400, planned.content)
        self.assertIn("bounded plan limit", planned.json()["detail"])
        self.assertFalse(slice_io.resolve_path(working_label_rel_path(self.volume)).exists())

    def test_deleting_prompt_does_not_delete_parent_pixels(self):
        client = self._client(self.annotator)
        payload = self._prompt_payload(17)
        client.put(
            f"/api/tasks/{self.task.id}/track/prompts/", payload, format="json"
        )
        # Existing final-label data is a separate working-mask concern.
        ids = [0] * 100
        ids[0] = 17
        runs = []
        start = 0
        for i in range(1, 101):
            if i == 100 or ids[i] != ids[start]:
                runs.append([ids[start], i - start])
                start = i
        client.put(
            f"/api/tasks/{self.task.id}/label-ids/",
            {"axis": "z", "index": 2, "shape": [10, 10], "runs": runs},
            format="json",
        )
        response = client.delete(
            f"/api/tasks/{self.task.id}/track/prompts/?parent_id=17"
        )
        self.assertEqual(response.status_code, 200, response.content)
        label_slice = client.get(
            f"/api/tasks/{self.task.id}/label-ids/?axis=z&index=2"
        )
        self.assertEqual(label_slice.json()["runs"], runs)

    def test_prompt_queue_replace_is_atomic_and_recomputes_seed_range(self):
        client = self._client(self.annotator)
        payload = self._prompt_payload(17)
        payload["z_range"] = [0, 99]
        replaced = client.post(
            f"/api/tasks/{self.task.id}/track/prompts/",
            {"items": [payload]},
            format="json",
        )
        self.assertEqual(replaced.status_code, 200, replaced.content)
        self.assertEqual(replaced.json()["items"][0]["z_range"], [2, 3])
        self.volume.refresh_from_db()
        self.assertEqual(
            self.volume.metadata["tracking_prompts"]["items"][0]["subclasses"],
            payload["subclasses"],
        )

    def test_requester_cannot_track(self):
        resp = self._client(self.requester).post(
            f"/api/tasks/{self.task.id}/track/", self._seed_payload(), format="json"
        )
        self.assertEqual(resp.status_code, 403)

    def test_requester_cannot_mutate_tracking_queue_or_batch(self):
        client = self._client(self.requester)
        queued = client.put(
            f"/api/tasks/{self.task.id}/track/prompts/",
            self._prompt_payload(17),
            format="json",
        )
        self.assertEqual(queued.status_code, 403)
        replaced = client.post(
            f"/api/tasks/{self.task.id}/track/prompts/",
            {"items": [self._prompt_payload(17)]},
            format="json",
        )
        self.assertEqual(replaced.status_code, 403)
        batch = client.post(
            f"/api/tasks/{self.task.id}/track/batch/",
            {"parent_ids": [17]},
            format="json",
        )
        self.assertEqual(batch.status_code, 403)

    def test_annotator_can_track_as_read_only_plan(self):
        resp = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/track/", self._seed_payload(), format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIn("final_id", resp.json()["results"][0])
        self.assertTrue(resp.json()["slices"])
        self.volume.refresh_from_db()
        self.assertEqual(self.volume.label_path, "")
        working_path = slice_io.resolve_path(working_label_rel_path(self.volume))
        self.assertFalse(working_path.exists())

    def test_annotator_can_paint_and_persist_label_ids(self):
        c = self._client(self.annotator)
        resp = c.get(f"/api/tasks/{self.task.id}/label-ids/?axis=z&index=2")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["shape"], [10, 10])
        self.assertEqual(body["runs"], [[0, 100]])  # empty label, all background

        # Paint a 2x2 block of instance id 3 at the top-left, RLE-encoded.
        ids = [0] * 100
        for y in (0, 1):
            for x in (0, 1):
                ids[y * 10 + x] = 3
        runs = []
        start = 0
        for i in range(1, 101):
            if i == 100 or ids[i] != ids[start]:
                runs.append([ids[start], i - start])
                start = i

        resp = c.put(
            f"/api/tasks/{self.task.id}/label-ids/",
            {"axis": "z", "index": 2, "shape": [10, 10], "runs": runs},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["max_label_id"], 3)

        resp = c.get(f"/api/tasks/{self.task.id}/label-ids/?axis=z&index=2")
        self.assertEqual(resp.json()["runs"], runs)

        resp = c.get(f"/api/tasks/{self.task.id}/label-state/")
        self.assertEqual(resp.json(), {"max_label_id": 3, "next_label_id": 4})

    def test_requester_cannot_edit_label_ids(self):
        resp = self._client(self.requester).put(
            f"/api/tasks/{self.task.id}/label-ids/",
            {"axis": "z", "index": 2, "shape": [10, 10], "runs": [[0, 100]]},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_requester_can_view_label_ids_but_not_edit(self):
        resp = self._client(self.requester).get(
            f"/api/tasks/{self.task.id}/label-ids/?axis=z&index=2"
        )
        self.assertEqual(resp.status_code, 200)

    def test_edit_never_mutates_an_externally_referenced_label_file(self):
        # A volume whose label is registered *by reference* to a file this
        # app doesn't own (e.g. someone else's prediction/consensus output),
        # living outside MITO_DATA_ROOT entirely — exactly the shape of the
        # incident this test guards against.
        external_dir = tempfile.mkdtemp(prefix="mito-external-owner-")
        external_path = os.path.join(external_dir, "someone_elses_consensus.tif")
        original = np.full((6, 10, 10), 7, dtype=np.uint16)
        tifffile.imwrite(external_path, original)
        original_bytes = open(external_path, "rb").read()

        self.volume.label_path = external_path
        self.volume.save(update_fields=["label_path"])

        c = self._client(self.annotator)

        # A real client always reads the (seeded) slice first, patches the
        # one pixel it cares about, and PUTs the whole slice back — this is
        # what proves seeding from the external file actually happened.
        got = c.get(f"/api/tasks/{self.task.id}/label-ids/?axis=z&index=0")
        self.assertEqual(got.status_code, 200, got.content)
        body = got.json()
        self.assertEqual(body["runs"], [[7, 100]])  # seeded from the external file

        ids = [7] * 100
        ids[0] = 9
        runs = []
        start = 0
        for i in range(1, 101):
            if i == 100 or ids[i] != ids[start]:
                runs.append([ids[start], i - start])
                start = i
        resp = c.put(
            f"/api/tasks/{self.task.id}/label-ids/",
            {"axis": "z", "index": 0, "shape": [10, 10], "runs": runs},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        # The external file must be byte-for-byte unchanged...
        self.assertEqual(open(external_path, "rb").read(), original_bytes)

        # ...and volume.label_path (the *official* label) must be completely
        # untouched too — an in-app edit only ever writes the working copy;
        # nothing is promoted to official until a submission is approved
        # (see test_inapp_submit_and_approve_promotes_working_copy_to_official).
        self.volume.refresh_from_db()
        self.assertEqual(self.volume.label_path, external_path)

        # The edit lives in the working copy, seeded from the external file.
        working_rel = working_label_rel_path(self.volume)
        edited = slice_io.read_slice(working_rel, "z", 0)
        self.assertEqual(edited[0, 0], 9)
        self.assertEqual(edited[0, 1], 7)  # seeded from the original elsewhere

    def test_inapp_submit_requires_prior_edit(self):
        resp = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/submit-inapp/", {}, format="json"
        )
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_inapp_submit_and_approve_promotes_working_copy_to_official(self):
        c = self._client(self.annotator)
        # Paint one pixel so a working copy exists.
        ids = [0] * 100
        ids[0] = 5
        runs = [[5, 1], [0, 99]]
        resp = c.put(
            f"/api/tasks/{self.task.id}/label-ids/",
            {"axis": "z", "index": 0, "shape": [10, 10], "runs": runs},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        resp = c.post(f"/api/tasks/{self.task.id}/submit-inapp/", {}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body["source"], "inapp")
        self.assertTrue(body["label_file"], "online review must own an immutable snapshot")

        # Before approval: still not the official label.
        self.volume.refresh_from_db()
        self.assertEqual(self.volume.label_path, "")

        review = self._client(self.manager).post(
            f"/api/submissions/{body['id']}/review/",
            {"decision": "approved"}, format="json",
        )
        self.assertEqual(review.status_code, 200, review.content)

        # After approval: the snapshot is copied to an app-owned official path,
        # then the editable working copy is freshly seeded from it.
        self.volume.refresh_from_db()
        self.assertIn("/approved/", self.volume.label_path)
        self.assertNotEqual(self.volume.label_path, working_label_rel_path(self.volume))
        self.assertEqual(self.volume.label_type, LabelType.PARTIAL)
        official = slice_io.read_slice(self.volume.label_location, "z", 0)
        working = slice_io.read_slice(working_label_rel_path(self.volume), "z", 0)
        self.assertEqual(official[0, 0], 5)
        np.testing.assert_array_equal(working, official)

    def test_inapp_reject_does_not_promote(self):
        c = self._client(self.annotator)
        resp = c.put(
            f"/api/tasks/{self.task.id}/label-ids/",
            {"axis": "z", "index": 0, "shape": [10, 10], "runs": [[5, 1], [0, 99]]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        resp = c.post(f"/api/tasks/{self.task.id}/submit-inapp/", {}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        submission_id = resp.json()["id"]

        review = self._client(self.manager).post(
            f"/api/submissions/{submission_id}/review/",
            {"decision": "rejected"}, format="json",
        )
        self.assertEqual(review.status_code, 200, review.content)

        self.volume.refresh_from_db()
        self.assertEqual(self.volume.label_path, "")  # never promoted

    def test_requester_can_view_slices(self):
        # Default (no window/level): JPEG, normalised client-side — see
        # VolumeSliceView. Explicit window/level still returns PNG.
        resp = self._client(self.requester).get(
            f"/api/volumes/{self.volume.id}/slice/?axis=z&index=2"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/jpeg")

        resp = self._client(self.requester).get(
            f"/api/volumes/{self.volume.id}/slice/?axis=z&index=2&window=255&level=128"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")


class LabelPathLayoutTests(TestCase):
    """Unit tests for the project/dataset-nested working-copy path scheme —
    no API/DB fixtures beyond the models themselves needed."""

    def test_mask_name_derives_from_image_basename(self):
        from annotation.label_paths import (
            volume_embeddings_dir_rel_path,
            working_label_metadata_rel_path,
        )
        from projects.models import Dataset, Project
        from volumes.models import Volume

        project = Project.objects.create(title="My Cool Project!!")
        dataset = Dataset.objects.create(project=project, name="Batch #1")
        volume = Volume.objects.create(
            project=project,
            dataset=dataset,
            name="v",
            image_path="raw/2026-02-18_18-03__heart__volume.ome.tif",
        )

        # Names are used as-is (not slugified/lowercased); only the final .tif
        # is stripped (the inner .ome stays), then _mask.tif is appended.
        rel = working_label_rel_path(volume)
        self.assertEqual(
            rel,
            "My Cool Project!!/Batch #1/2026-02-18_18-03__heart__volume.ome_mask.tif",
        )
        # Metadata sidecar lives in a metadata/ subfolder beside the mask.
        self.assertEqual(
            working_label_metadata_rel_path(volume),
            "My Cool Project!!/Batch #1/metadata/"
            "2026-02-18_18-03__heart__volume.ome_mask_metadata.json",
        )
        # Embeddings live under the dataset folder, not a global silo.
        self.assertEqual(
            volume_embeddings_dir_rel_path(volume),
            "My Cool Project!!/Batch #1/embeddings",
        )

    def test_colliding_stems_disambiguate_by_id(self):
        from projects.models import Dataset, Project
        from volumes.models import Volume

        project = Project.objects.create(title="P")
        dataset = Dataset.objects.create(project=project, name="D")
        # Two volumes in one dataset folder sharing an image basename.
        a = Volume.objects.create(
            project=project, dataset=dataset, name="a", image_path="a/img.tif"
        )
        b = Volume.objects.create(
            project=project, dataset=dataset, name="b", image_path="b/img.tif"
        )
        # Lowest id keeps the plain stem; the later one disambiguates with _v<id>.
        self.assertEqual(working_label_rel_path(a), "P/D/img_mask.tif")
        self.assertEqual(working_label_rel_path(b), f"P/D/img_v{b.id}_mask.tif")

    def test_path_falls_back_to_no_dataset_bucket(self):
        from projects.models import Project
        from volumes.models import Volume

        project = Project.objects.create(title="Solo Project")
        volume = Volume.objects.create(project=project, name="v")  # no dataset, no image

        # No image at all -> volume_<id> fallback stem (edge case).
        rel = working_label_rel_path(volume)
        self.assertEqual(rel, f"Solo Project/no-dataset/volume_{volume.id}_mask.tif")

    def test_slug_cannot_escape_data_root(self):
        from pathlib import Path

        from django.conf import settings

        from annotation.visualization.slice_io import resolve_path
        from projects.models import Project
        from volumes.models import Volume

        # A title crafted to try to escape the data root if not sanitized.
        project = Project.objects.create(title="../../etc/passwd")
        volume = Volume.objects.create(project=project, name="v")

        rel = working_label_rel_path(volume)
        resolved = resolve_path(rel).resolve()
        root = Path(settings.MITO_DATA_ROOT).resolve()
        # However the project's title got sanitized, the result must still
        # resolve to exactly root/<project dir>/<dataset dir>/<file> — i.e.
        # never navigate above root no matter what a project is named.
        self.assertEqual(resolved.parents[2], root)


@override_settings(MITO_DATA_ROOT=_TMP, MEDIA_ROOT=_TMP)
class HardCaseApiTests(TestCase):
    """Project hard cases: auth-gated create, project-scoped visibility,
    creator/manager-only annotate + take-down, and the public read-only token
    link that still works for people outside the app.

    See progress/history/{02-share-hard-case,05-submit-people-hardcases}.md.
    """

    def setUp(self):
        slice_io.clear_caches()
        self.manager = self._user("hc_mgr", UserRole.MANAGER)
        self.annotator = self._user("hc_ann", UserRole.ANNOTATOR, annotator=True)
        self.peer = self._user("hc_ann2", UserRole.ANNOTATOR, annotator=True)
        self.outsider = self._user("hc_ann3", UserRole.ANNOTATOR, annotator=True)
        self.requester = self._user("hc_req", UserRole.REQUESTER)

        self.project = create_project(title="HC", created_by=self.requester, reviewed=True)
        rel = "images/hc.tif"
        path = os.path.join(_TMP, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tifffile.imwrite(path, np.full((6, 10, 10), 120, dtype=np.uint8))
        self.volume = Volume.objects.create(
            project=self.project, name="v", image_path=rel,
            label_type=LabelType.NONE, shape_z=6, shape_y=10, shape_x=10,
        )
        owned = os.path.join(_TMP, working_label_rel_path(self.volume))
        if os.path.exists(owned):
            os.remove(owned)
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume, assigned_to=self.annotator,
            z_start=0, z_end=6, y_end=10, x_end=10,
            task_type=TaskType.MANUAL_ANNOTATION,
        )
        # A second task on the same project makes `peer` a project member
        # without giving them access to `self.task` — the exact case the
        # project-membership visibility rule exists for.
        self.peer_volume = Volume.objects.create(
            project=self.project, name="v2", image_path=rel,
            label_type=LabelType.NONE, shape_z=6, shape_y=10, shape_x=10,
        )
        self.peer_task = AnnotationTask.objects.create(
            project=self.project, volume=self.peer_volume, assigned_to=self.peer,
            z_start=0, z_end=6, y_end=10, x_end=10,
            task_type=TaskType.MANUAL_ANNOTATION,
        )
        # Cases can only be recorded for ids that really exist in the mask, so
        # paint labels 3 and 7 on one slice.
        self._paint(3, rows=slice(0, 3))
        self._paint(7, rows=slice(5, 8))

    def _paint(self, label_id, rows):
        """Paint ``label_id`` on z=0 through the same service the editor uses,
        so the volume really contains the ids the create endpoint validates
        against."""
        from annotation.services import get_label_slice_ids, set_label_slice_ids
        from annotation.visualization.slice_io import (
            decode_label_rle,
            encode_label_rle,
        )

        current = decode_label_rle(
            get_label_slice_ids(self.volume, "z", 0)["runs"], (10, 10)
        )
        current[rows, 0:4] = label_id
        set_label_slice_ids(self.volume, "z", 0, [10, 10], encode_label_rle(current))

    def _user(self, name, role, annotator=False):
        user = User.objects.create_user(name, password="x")
        UserProfile.objects.filter(user=user).update(role=role)
        if annotator:
            AnnotatorProfile.objects.create(user=user, is_active_annotator=True)
        return User.objects.get(pk=user.pk)

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def _create_case(self, user, label_id=7, task=None, note=None):
        return self._client(user).post(
            f"/api/tasks/{(task or self.task).id}/hard-cases/",
            {"label_id": label_id, **({"note": note} if note is not None else {})},
            format="json",
        )

    def test_hard_case_note_is_optional_and_returned_everywhere(self):
        without = self._create_case(self.annotator, label_id=3)
        self.assertEqual(without.status_code, 201, without.content)
        self.assertEqual(without.json()["note"], "")

        created = self._create_case(
            self.annotator, label_id=7, note="  boundary is ambiguous near cristae  "
        )
        self.assertEqual(created.status_code, 201, created.content)
        row = created.json()
        self.assertEqual(row["note"], "boundary is ambiguous near cristae")
        listed = self._client(self.annotator).get("/api/hard-cases/").json()
        self.assertEqual(next(item for item in listed if item["id"] == row["id"])["note"], row["note"])
        detail = self._client(self.annotator).get(f"/api/hard-cases/{row['id']}/")
        self.assertEqual(detail.json()["note"], row["note"])
        public = APIClient().get(f"/api/public/hard-cases/{row['token']}/meta/")
        self.assertEqual(public.json()["note"], row["note"])

    def test_full_task_share_is_public_read_only_and_permission_scoped(self):
        created = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/share/", {}, format="json"
        )
        self.assertEqual(created.status_code, 200, created.content)
        token = created.json()["token"]
        anonymous = APIClient()
        meta = anonymous.get(f"/api/public/tasks/{token}/meta/")
        self.assertEqual(meta.status_code, 200, meta.content)
        self.assertEqual(meta.json()["task_id"], self.task.id)
        self.assertIsNone(meta.json()["label_id"])
        summary = anonymous.get(
            f"/api/public/tasks/{token}/labels-summary/"
        )
        self.assertEqual(
            {row["id"] for row in summary.json()["labels"]}, {3, 7}
        )
        self.assertEqual(
            anonymous.put(
                f"/api/public/tasks/{token}/label-ids/",
                {"axis": "z", "index": 0, "shape": [10, 10], "runs": []},
                format="json",
            ).status_code,
            405,
        )
        self.assertEqual(
            self._client(self.outsider).post(
                f"/api/tasks/{self.task.id}/share/", {}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            anonymous.get("/api/public/tasks/not-valid/meta/").status_code, 404
        )

    def test_explicit_zero_task_member_sees_hard_cases_without_workload(self):
        from annotation.services import calculate_annotator_workload
        from projects.models import ProjectMembership

        created = self._create_case(self.annotator, label_id=7)
        self.assertEqual(created.status_code, 201, created.content)
        case_id = created.json()["id"]
        self.assertEqual(self._client(self.outsider).get("/api/hard-cases/").json(), [])

        membership = ProjectMembership.objects.create(
            project=self.project, user=self.outsider, added_by=self.manager
        )
        visible = self._client(self.outsider).get("/api/hard-cases/")
        self.assertEqual(visible.status_code, 200, visible.content)
        self.assertEqual([row["id"] for row in visible.json()], [case_id])
        self.assertNotIn(
            self.outsider.id,
            {row["annotator_id"] for row in calculate_annotator_workload(self.project)},
        )

        membership.delete()
        self.assertEqual(self._client(self.outsider).get("/api/hard-cases/").json(), [])

    def test_manager_can_add_list_and_remove_explicit_member(self):
        manager = self._client(self.manager)
        url = f"/api/projects/{self.project.id}/members/"
        added = manager.post(url, {"user_id": self.outsider.id}, format="json")
        self.assertEqual(added.status_code, 201, added.content)
        rows = manager.get(url).json()
        outsider = next(row for row in rows if row["user_id"] == self.outsider.id)
        self.assertTrue(outsider["is_explicit"])
        self.assertFalse(outsider["has_tasks"])
        self.assertEqual(
            manager.delete(f"{url}{self.outsider.id}/").status_code, 204
        )

    def test_public_read_of_official_label_does_not_create_working_copy(self):
        official_rel = "labels/official-only.tif"
        official_path = os.path.join(_TMP, official_rel)
        os.makedirs(os.path.dirname(official_path), exist_ok=True)
        labels = np.zeros((6, 10, 10), dtype=np.uint16)
        labels[0, 2:4, 2:4] = 9
        tifffile.imwrite(official_path, labels)
        volume = Volume.objects.create(
            project=self.project,
            name="official-only",
            image_path="images/hc.tif",
            label_path=official_rel,
            label_type=LabelType.PARTIAL,
            shape_z=6,
            shape_y=10,
            shape_x=10,
        )
        task = AnnotationTask.objects.create(
            project=self.project,
            volume=volume,
            assigned_to=self.annotator,
            z_start=0,
            z_end=6,
            y_end=10,
            x_end=10,
            task_type=TaskType.MANUAL_ANNOTATION,
        )
        working = os.path.join(_TMP, working_label_rel_path(volume))
        self.assertFalse(os.path.exists(working))
        token = self._client(self.annotator).post(
            f"/api/tasks/{task.id}/share/", {}, format="json"
        ).json()["token"]
        response = APIClient().get(
            f"/api/public/tasks/{token}/label-ids/?axis=z&index=0"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(os.path.exists(working))

    # --- create (auth-gated) ------------------------------------------------

    def test_assigned_annotator_can_create_case(self):
        resp = self._create_case(self.annotator, label_id=7)
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body["label_id"], 7)
        self.assertTrue(body["token"])
        self.assertEqual(body["url"], f"/share/hard-case/{body['token']}")
        self.assertEqual(body["app_url"], f"/hard-cases/{body['id']}")
        # Project + volume are denormalized so the lists never join through tasks.
        self.assertEqual(body["project"], self.project.id)
        self.assertEqual(body["volume"], self.volume.id)
        self.assertEqual(body["status"], "open")

    def test_manager_can_create_case(self):
        self.assertEqual(self._create_case(self.manager).status_code, 201)

    def test_requester_cannot_create_case(self):
        self.assertEqual(self._create_case(self.requester).status_code, 403)

    def test_unassigned_annotator_cannot_create_case(self):
        self.assertEqual(self._create_case(self.peer).status_code, 403)

    def test_anonymous_cannot_create_case(self):
        resp = APIClient().post(
            f"/api/tasks/{self.task.id}/hard-cases/",
            {"label_id": 7}, format="json",
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_create_requires_valid_label_id(self):
        self.assertEqual(self._create_case(self.annotator, label_id=0).status_code, 400)

    def test_create_rejects_a_label_that_does_not_exist(self):
        self.assertEqual(self._create_case(self.annotator, label_id=999).status_code, 400)

    def test_locked_task_can_still_have_a_case_recorded(self):
        """Flagging a hard case is not annotating it — an approved-and-closed
        task is exactly when you might want to raise one."""
        self.task.annotation_locked = True
        self.task.save(update_fields=["annotation_locked"])
        self.assertEqual(self._create_case(self.annotator).status_code, 201)

    # --- project-scoped visibility + permissions ----------------------------

    def test_every_project_member_sees_the_case_and_outsiders_do_not(self):
        self._create_case(self.annotator)
        for user in (self.annotator, self.peer, self.manager, self.requester):
            rows = self._client(user).get("/api/hard-cases/").json()
            self.assertEqual(len(rows), 1, f"{user.username} should see the case")
        self.assertEqual(
            self._client(self.outsider).get("/api/hard-cases/").json(), []
        )

    def test_list_is_newest_first(self):
        first = self._create_case(self.annotator, label_id=3).json()
        second = self._create_case(self.annotator, label_id=7).json()
        rows = self._client(self.manager).get("/api/hard-cases/").json()
        self.assertEqual([r["id"] for r in rows], [second["id"], first["id"]])

    def test_list_filters_by_project(self):
        case = self._create_case(self.annotator).json()
        rows = self._client(self.manager).get(
            f"/api/hard-cases/?project={self.project.id}"
        ).json()
        self.assertEqual([r["id"] for r in rows], [case["id"]])
        self.assertEqual(
            self._client(self.manager).get("/api/hard-cases/?project=99999").json(), []
        )
        # A junk id is "no such project" — never a silently unfiltered list.
        self.assertEqual(
            self._client(self.manager).get("/api/hard-cases/?project=abc").json(), []
        )

    def test_only_creator_and_manager_may_annotate_or_take_down(self):
        case_id = self._create_case(self.annotator).json()["id"]
        for user, may in ((self.annotator, True), (self.manager, True),
                          (self.peer, False), (self.requester, False)):
            body = self._client(user).get(f"/api/hard-cases/{case_id}/").json()
            self.assertEqual(body["can_annotate"], may, user.username)
            self.assertEqual(body["can_take_down"], may, user.username)

    def test_creator_loses_annotate_when_the_task_is_locked(self):
        """A case is not a separate document — annotating one writes the task's
        working copy, so the button must follow the task's own lock."""
        case_id = self._create_case(self.annotator).json()["id"]
        self.task.annotation_locked = True
        self.task.save(update_fields=["annotation_locked"])
        body = self._client(self.annotator).get(f"/api/hard-cases/{case_id}/").json()
        self.assertFalse(body["can_annotate"])
        # Take-down is about the case, not the pixels — still allowed.
        self.assertTrue(body["can_take_down"])

    def test_outsider_cannot_open_a_case(self):
        case_id = self._create_case(self.annotator).json()["id"]
        self.assertEqual(
            self._client(self.outsider).get(f"/api/hard-cases/{case_id}/").status_code,
            403,
        )

    def test_take_down_resolves_without_deleting(self):
        case_id = self._create_case(self.annotator).json()["id"]
        resp = self._client(self.annotator).post(
            f"/api/hard-cases/{case_id}/status/", {"status": "resolved"}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["status"], "resolved")
        self.assertEqual(resp.json()["resolved_by_username"], "hc_ann")
        # Still listed and still readable by everyone on the project.
        rows = self._client(self.peer).get("/api/hard-cases/").json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "resolved")

    def test_peer_cannot_take_a_case_down(self):
        case_id = self._create_case(self.annotator).json()["id"]
        resp = self._client(self.peer).post(
            f"/api/hard-cases/{case_id}/status/", {"status": "resolved"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_resolved_case_can_be_reopened(self):
        case_id = self._create_case(self.annotator).json()["id"]
        self._client(self.manager).post(
            f"/api/hard-cases/{case_id}/status/", {"status": "resolved"}, format="json",
        )
        resp = self._client(self.manager).post(
            f"/api/hard-cases/{case_id}/status/", {"status": "open"}, format="json",
        )
        self.assertEqual(resp.json()["status"], "open")
        self.assertIsNone(resp.json()["resolved_at"])

    # --- public read (anonymous, token-only) --------------------------------

    def _token(self):
        return self._create_case(self.annotator, label_id=3).json()["token"]

    def test_public_meta_works_without_account(self):
        token = self._token()
        resp = APIClient().get(f"/api/public/hard-cases/{token}/meta/")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["label_id"], 3)
        self.assertEqual(body["task_id"], self.task.id)
        self.assertEqual(body["volume_id"], self.volume.id)
        self.assertEqual(body["z_start"], 0)
        self.assertEqual(body["shape"], {"z": 6, "y": 10, "x": 10})

    def test_public_slice_and_labels_are_anonymous(self):
        token = self._token()
        anon = APIClient()
        s = anon.get(f"/api/public/hard-cases/{token}/slice/?axis=z&index=2")
        self.assertEqual(s.status_code, 200)
        self.assertEqual(s["Content-Type"], "image/jpeg")
        ids = anon.get(f"/api/public/hard-cases/{token}/label-ids/?axis=z&index=2")
        self.assertEqual(ids.status_code, 200)
        summ = anon.get(f"/api/public/hard-cases/{token}/labels-summary/")
        self.assertEqual(summ.status_code, 200)
        d3 = anon.post(
            f"/api/public/hard-cases/{token}/labels-3d/", {"labels": [3]}, format="json",
        )
        self.assertEqual(d3.status_code, 200)
        self.assertEqual(d3["Content-Type"], "application/octet-stream")

    def test_public_3d_mesh_is_anonymous_and_well_formed(self):
        """The shared viewer renders the same iso-surface payload the authed
        one does (03 item B: one mesh path for Annotate / View / share)."""
        import struct

        token = self._token()
        resp = APIClient().post(
            f"/api/public/hard-cases/{token}/labels-3d-mesh/", {"labels": [3]}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp["Content-Type"], "application/octet-stream")
        version, _num_meshes, _truncated, _reserved = struct.unpack_from("<IIII", resp.content, 0)
        self.assertEqual(version, 1)

    def test_mesh_endpoint_404s_on_a_bad_token(self):
        resp = APIClient().post(
            "/api/public/hard-cases/nope/labels-3d-mesh/", {"labels": [1]}, format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_bad_token_is_404(self):
        self.assertEqual(
            APIClient().get("/api/public/hard-cases/not-a-real-token/meta/").status_code,
            404,
        )

    def test_revoking_kills_the_public_link_but_not_project_access(self):
        case = self._create_case(self.annotator, label_id=3).json()
        resp = self._client(self.annotator).post(
            f"/api/hard-cases/{case['id']}/revoke/", {"revoked": True}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(
            APIClient().get(f"/api/public/hard-cases/{case['token']}/meta/").status_code,
            404,
        )
        # Project members are unaffected — revoke is about the public token.
        self.assertEqual(
            self._client(self.peer).get(f"/api/hard-cases/{case['id']}/").status_code,
            200,
        )

    # --- no public write path -----------------------------------------------

    def test_public_namespace_has_no_write_endpoint(self):
        token = self._token()
        anon = APIClient()
        # There is only a GET for label-ids under the public namespace; a PUT
        # (the authed editor's write) must not exist there.
        put = anon.put(
            f"/api/public/hard-cases/{token}/label-ids/",
            {"axis": "z", "index": 0, "shape": [10, 10], "runs": []},
            format="json",
        )
        self.assertEqual(put.status_code, 405)
        # And the authed write endpoint still requires login (token is useless there).
        authed_put = anon.put(
            f"/api/tasks/{self.task.id}/label-ids/",
            {"axis": "z", "index": 0, "shape": [10, 10], "runs": []},
            format="json",
        )
        self.assertIn(authed_put.status_code, (401, 403))
