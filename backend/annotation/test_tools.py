"""Phase 9 — the ranked P1 annotation tools.

Doc 19's P1 rows, which §E9 names as the scope: flood fill 2D/3D, overwrite
policies, deep links. Gate is "ranked P1 done", so this module covers exactly
those three and nothing below the line.

Golden expectations come from `tools/golden/make_tool_fixtures.py`, which builds
them by hand from geometry and never imports the core.
"""

from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path

import numpy as np
from django.contrib.auth.models import User
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings

from accounts.models import AnnotatorProfile, UserProfile
from annotation.models import AnnotationOperation, AnnotationTask
from annotation.operations import VersionConflict, current_version
from annotation.tools import deeplinks, flood_fill, service
from annotation.tools.common import BoundingBox, ToolError
from annotation.tools.overwrite import (
    DEFAULT_OVERWRITE_MODE,
    OVERWRITE_ALL,
    OVERWRITE_EMPTY,
    writable_mask,
)
from core.choices import TaskStatus, UserRole
from projects.models import Dataset, Project
from volumes.models import Volume

GOLDEN = Path(__file__).resolve().parent / "tools" / "golden"
MANIFEST = json.loads((GOLDEN / "manifest.json").read_text())

# Tools on, the Phase 7 operations log deliberately off — stated, not left
# to the settings default. FEATURE_ANNOTATION_OPS is *on* in the deployed
# `production_integrated_v1` profile, so PlanOnlyTests relied on a default
# that only holds outside production and failed under the live profile.
TOOLS_ON = override_settings(FEATURE_ANNOTATION_TOOLS=True,
                             FEATURE_ANNOTATION_OPS=False)
TOOLS_OFF = override_settings(FEATURE_ANNOTATION_TOOLS=False)
FULL_ON = override_settings(FEATURE_ANNOTATION_TOOLS=True,
                            FEATURE_ANNOTATION_OPS=True)

requires_postgres = unittest.skipUnless(
    connection.vendor == "postgresql", "row locking is PostgreSQL-only")


def box(d, h, w):
    return BoundingBox(0, 0, 0, d, h, w)


# ---------------------------------------------------------------------------
# P1.1 — flood fill: golden
# ---------------------------------------------------------------------------


class FloodFillGoldenTests(TestCase):
    def test_manifest_matches_disk(self):
        self.assertEqual(
            {p.name for p in GOLDEN.glob("*.npz")},
            {c["fixture"] for c in MANIFEST["cases"]},
        )

    def test_fixtures_match_recorded_hashes(self):
        import hashlib

        for case in MANIFEST["cases"]:
            z = np.load(GOLDEN / case["fixture"])
            for key, expected in case["hashes"].items():
                actual = hashlib.sha256(
                    np.ascontiguousarray(z[key]).tobytes()).hexdigest()
                self.assertEqual(actual, expected, f"{case['id']}:{key}")

    def test_generator_does_not_import_the_core(self):
        source = (GOLDEN / "make_tool_fixtures.py").read_text()
        for forbidden in ("from annotation.tools", "import flood_fill",
                          "tools.flood_fill"):
            self.assertNotIn(forbidden, source)

    def test_every_golden_case_matches_exactly(self):
        """Flood fill is discrete — no tolerance, exact equality."""
        for case in MANIFEST["cases"]:
            z = np.load(GOLDEN / case["fixture"])
            got = flood_fill.fill_mask(z["block"], tuple(case["seed"]))
            np.testing.assert_array_equal(
                got, z["expected"], err_msg=f"golden case {case['id']}")

    def test_four_connectivity_does_not_leak_diagonally(self):
        """The case that pins the connectivity choice."""
        case = next(c for c in MANIFEST["cases"] if c["id"] == "diagonal_no_leak")
        z = np.load(GOLDEN / case["fixture"])
        got = flood_fill.fill_mask(z["block"], tuple(case["seed"]))
        self.assertFalse(got[0, :, 4:].any(),
                         "fill leaked past a solid wall")


# ---------------------------------------------------------------------------
# P1.1 — flood fill: core behaviour
# ---------------------------------------------------------------------------


class FloodFillCoreTests(TestCase):
    def setUp(self):
        self.block = np.zeros((1, 8, 8), np.uint8)

    def test_deterministic_across_runs(self):
        b = np.zeros((1, 16, 16), np.uint8)
        b[0, 8, :] = 9
        a = flood_fill.fill_mask(b, (0, 2, 2))
        c = flood_fill.fill_mask(b, (0, 2, 2))
        np.testing.assert_array_equal(a, c)

    def test_seed_order_does_not_matter_within_a_component(self):
        """Any seed in a component yields the same component."""
        b = np.zeros((1, 8, 8), np.uint8)
        b[0, :, 4] = 9
        np.testing.assert_array_equal(
            flood_fill.fill_mask(b, (0, 0, 0)),
            flood_fill.fill_mask(b, (0, 7, 3)),
        )

    def test_seed_out_of_bounds(self):
        with self.assertRaises(ToolError) as c:
            flood_fill.fill_mask(self.block, (0, 99, 0))
        self.assertEqual(c.exception.reason, "seed_out_of_bounds")

    def test_negative_seed_is_out_of_bounds(self):
        with self.assertRaises(ToolError):
            flood_fill.fill_mask(self.block, (0, -1, 0))

    def test_two_dimensional_block_required_shape(self):
        with self.assertRaises(ToolError) as c:
            flood_fill.fill_mask(np.zeros((8, 8), np.uint8), (0, 0))
        self.assertEqual(c.exception.reason, "bad_rank")

    def test_float_dtype_is_rejected(self):
        with self.assertRaises(ToolError) as c:
            flood_fill.fill_mask(np.zeros((1, 4, 4), np.float32), (0, 0, 0))
        self.assertEqual(c.exception.reason, "bad_dtype")

    def test_plan_reports_voxels_and_params(self):
        plan = flood_fill.plan(self.block, seed=(0, 4, 4), label_id=3,
                               bbox=box(1, 8, 8))
        self.assertEqual(plan.voxels_changed, 64)
        self.assertEqual(plan.params["seed"], [0, 4, 4])
        self.assertEqual(plan.params["connectivity"], 4)

    def test_three_d_reports_six_connectivity(self):
        plan = flood_fill.plan(np.zeros((3, 4, 4), np.uint8), seed=(0, 0, 0),
                               label_id=3, bbox=box(3, 4, 4))
        self.assertEqual(plan.params["connectivity"], 6)

    def test_plan_does_not_mutate_the_block(self):
        before = self.block.copy()
        flood_fill.plan(self.block, seed=(0, 1, 1), label_id=3,
                        bbox=box(1, 8, 8))
        np.testing.assert_array_equal(self.block, before)

    def test_apply_writes_the_label(self):
        plan = flood_fill.plan(self.block, seed=(0, 4, 4), label_id=3,
                               bbox=box(1, 8, 8))
        out = flood_fill.apply_to_block(self.block, plan)
        self.assertTrue((out == 3).all())
        self.assertTrue((self.block == 0).all(), "input was mutated")

    def test_seed_already_target_label_warns_not_errors(self):
        b = np.zeros((1, 4, 4), np.uint8); b[:] = 3
        plan = flood_fill.plan(b, seed=(0, 1, 1), label_id=3, bbox=box(1, 4, 4),
                               overwrite_mode=OVERWRITE_ALL)
        self.assertTrue(any("already carries" in w for w in plan.warnings))

    def test_boundary_touching_seed(self):
        plan = flood_fill.plan(self.block, seed=(0, 0, 0), label_id=3,
                               bbox=box(1, 8, 8))
        self.assertEqual(plan.voxels_changed, 64)

    def test_non_square_plane(self):
        b = np.zeros((1, 4, 16), np.uint8)
        plan = flood_fill.plan(b, seed=(0, 0, 0), label_id=1, bbox=box(1, 4, 16))
        self.assertEqual(plan.voxels_changed, 64)

    def test_many_tiny_components_only_fills_one(self):
        b = np.zeros((1, 16, 16), np.uint8)
        b[0, ::2, ::2] = 0
        b[0, 1::2, :] = 9
        b[0, :, 1::2] = 9
        plan = flood_fill.plan(b, seed=(0, 0, 0), label_id=1, bbox=box(1, 16, 16))
        self.assertEqual(plan.voxels_changed, 1)


class FloodFillLimitTests(TestCase):
    def test_voxel_cap(self):
        b = np.zeros((1, 64, 64), np.uint8)
        with self.assertRaises(ToolError) as c:
            flood_fill.plan(b, seed=(0, 0, 0), label_id=1,
                            bbox=box(1, 64, 64), max_voxels=100)
        self.assertEqual(c.exception.reason, "too_large")

    def test_fill_depth_cap(self):
        b = np.zeros((40, 4, 4), np.uint8)
        with self.assertRaises(ToolError) as c:
            flood_fill.plan(b, seed=(0, 0, 0), label_id=1,
                            bbox=box(40, 4, 4), max_depth=32)
        self.assertEqual(c.exception.reason, "fill_depth_exceeded")

    def test_plane_dimension_cap(self):
        with self.assertRaises(ToolError) as c:
            BoundingBox(0, 0, 0, 1, 9000, 4).validate()
        self.assertEqual(c.exception.reason, "plane_too_large")

    def test_reserved_background_label(self):
        with self.assertRaises(ToolError) as c:
            flood_fill.plan(np.zeros((1, 4, 4), np.uint8), seed=(0, 0, 0),
                            label_id=0, bbox=box(1, 4, 4))
        self.assertEqual(c.exception.reason, "reserved_label")

    def test_label_dtype_overflow(self):
        with self.assertRaises(ToolError) as c:
            flood_fill.plan(np.zeros((1, 4, 4), np.uint8), seed=(0, 0, 0),
                            label_id=9999, bbox=box(1, 4, 4))
        self.assertEqual(c.exception.reason, "label_dtype_overflow")

    def test_negative_bbox_coordinate(self):
        with self.assertRaises(ToolError) as c:
            BoundingBox(-1, 0, 0, 1, 4, 4).validate()
        self.assertEqual(c.exception.reason, "negative_coordinate")

    def test_empty_bbox(self):
        with self.assertRaises(ToolError) as c:
            BoundingBox(0, 0, 0, 0, 4, 4).validate()
        self.assertEqual(c.exception.reason, "empty_bbox")

    def test_bbox_shape_mismatch(self):
        with self.assertRaises(ToolError) as c:
            flood_fill.plan(np.zeros((1, 4, 4), np.uint8), seed=(0, 0, 0),
                            label_id=1, bbox=box(1, 8, 8))
        self.assertEqual(c.exception.reason, "shape_mismatch")

    def test_bad_bbox_length(self):
        with self.assertRaises(ToolError) as c:
            BoundingBox.from_sequence([0, 0, 0])
        self.assertEqual(c.exception.reason, "bad_bbox")


# ---------------------------------------------------------------------------
# P1.2 — overwrite policies
# ---------------------------------------------------------------------------


class OverwritePolicyTests(TestCase):
    def setUp(self):
        self.labels = np.zeros((4, 4), np.uint8)
        self.labels[0:2, 0:2] = 5
        self.mask = np.ones((4, 4), bool)

    def test_default_is_conservative(self):
        self.assertEqual(DEFAULT_OVERWRITE_MODE, OVERWRITE_EMPTY)

    def test_overwrite_empty_protects_existing(self):
        out = writable_mask(self.labels, self.mask,
                            overwrite_mode=OVERWRITE_EMPTY)
        self.assertFalse(out[0:2, 0:2].any())
        self.assertTrue(out[2:, 2:].all())

    def test_overwrite_all_permits_everything(self):
        out = writable_mask(self.labels, self.mask,
                            overwrite_mode=OVERWRITE_ALL)
        self.assertTrue(out.all())

    def test_writable_mask_does_not_mutate(self):
        before = self.labels.copy()
        writable_mask(self.labels, self.mask, overwrite_mode=OVERWRITE_EMPTY)
        np.testing.assert_array_equal(self.labels, before)

    def test_flood_fill_honours_overwrite_empty(self):
        b = np.zeros((1, 4, 4), np.uint8)
        b[0, 0, 0] = 0
        b[0, 3, 3] = 0
        block = np.zeros((1, 4, 4), np.uint8)
        block[0, 1, 1] = 0
        # Put an existing label in the middle of the fill region.
        block[0, 2, 2] = 7
        plan = flood_fill.plan(block, seed=(0, 0, 0), label_id=3,
                               bbox=box(1, 4, 4),
                               overwrite_mode=OVERWRITE_EMPTY)
        out = flood_fill.apply_to_block(block, plan)
        self.assertEqual(int(out[0, 2, 2]), 7, "existing label was destroyed")

    def test_interpolation_still_uses_the_shared_policies(self):
        """The Phase 8 promotion must not have broken interpolation."""
        from annotation.interpolation import core as interp_core

        self.assertIs(interp_core.OVERWRITE_EMPTY, OVERWRITE_EMPTY)
        self.assertIs(interp_core.OVERWRITE_ALL, OVERWRITE_ALL)


# ---------------------------------------------------------------------------
# P1.3 — deep links
# ---------------------------------------------------------------------------


class DeepLinkTests(TestCase):
    def test_volume_link_round_trip(self):
        link = deeplinks.build(kind="volume", volume_id=7,
                               position=(3, 40, 50), label_id=12, task_id=9)
        parsed = deeplinks.parse(link)
        self.assertEqual(parsed.volume_id, 7)
        self.assertEqual((parsed.z, parsed.y, parsed.x), (3, 40, 50))
        self.assertEqual(parsed.label_id, 12)
        self.assertEqual(parsed.task_id, 9)

    def test_encoding_is_deterministic(self):
        a = deeplinks.build(kind="volume", volume_id=1, position=(1, 2, 3),
                            label_id=4)
        b = deeplinks.build(kind="volume", volume_id=1, position=(1, 2, 3),
                            label_id=4)
        self.assertEqual(a, b)

    def test_minimal_volume_link(self):
        parsed = deeplinks.parse(deeplinks.build(kind="volume", volume_id=3))
        self.assertEqual(parsed.volume_id, 3)
        self.assertFalse(parsed.has_position)

    def test_hard_case_link_round_trip(self):
        token = "a" * 32
        parsed = deeplinks.parse(deeplinks.build(kind="hard-case", token=token))
        self.assertEqual(parsed.kind, "hard-case")
        self.assertEqual(parsed.token, token)

    def test_bad_scheme(self):
        with self.assertRaises(ToolError) as c:
            deeplinks.parse("https://volume/1")
        self.assertEqual(c.exception.reason, "bad_scheme")

    def test_unknown_kind(self):
        with self.assertRaises(ToolError) as c:
            deeplinks.parse("mito://wat/1")
        self.assertEqual(c.exception.reason, "bad_kind")

    def test_non_integer_volume_id(self):
        with self.assertRaises(ToolError) as c:
            deeplinks.parse("mito://volume/abc")
        self.assertEqual(c.exception.reason, "bad_volume_id")

    def test_partial_position_is_rejected(self):
        """Sending the viewer somewhere the author did not mean is worse."""
        with self.assertRaises(ToolError) as c:
            deeplinks.parse("mito://volume/1?z=1&y=2")
        self.assertEqual(c.exception.reason, "incomplete_position")

    def test_negative_coordinate_rejected(self):
        with self.assertRaises(ToolError) as c:
            deeplinks.parse("mito://volume/1?z=-1&y=2&x=3")
        self.assertEqual(c.exception.reason, "negative_coordinate")

    def test_non_integer_parameter_rejected(self):
        with self.assertRaises(ToolError) as c:
            deeplinks.parse("mito://volume/1?z=a&y=2&x=3")
        self.assertEqual(c.exception.reason, "bad_parameter")

    def test_malformed_token_rejected(self):
        with self.assertRaises(ToolError) as c:
            deeplinks.parse("mito://hard-case/short")
        self.assertEqual(c.exception.reason, "bad_token")

    def test_oversized_link_rejected(self):
        with self.assertRaises(ToolError) as c:
            deeplinks.parse("mito://volume/1?z=1&y=2&x=" + "9" * 3000)
        self.assertEqual(c.exception.reason, "link_too_long")

    def test_empty_link_rejected(self):
        with self.assertRaises(ToolError):
            deeplinks.parse("")

    def test_parsing_grants_no_authority(self):
        """A DeepLink is a descriptor. It carries no user, role or permission."""
        parsed = deeplinks.parse("mito://volume/1?z=1&y=2&x=3")
        for attr in ("user", "actor", "permission", "allowed"):
            self.assertFalse(hasattr(parsed, attr))


# ---------------------------------------------------------------------------
# Service and Phase 7 integration
# ---------------------------------------------------------------------------


def make_user(name, role=UserRole.ANNOTATOR):
    u, _ = User.objects.get_or_create(username=name)
    u.set_password("pw-for-tests-1"); u.save()
    UserProfile.objects.update_or_create(user=u, defaults={"role": role})
    if role == UserRole.ANNOTATOR:
        AnnotatorProfile.objects.update_or_create(
            user=u, defaults={"is_active_annotator": True,
                              "max_active_tasks": 50})
    return User.objects.get(pk=u.pk)


class ServiceMixin:
    def build(self):
        self.user = make_user("tool-ann")
        self.project = Project.objects.create(title="Tools")
        self.dataset = Dataset.objects.create(project=self.project, name="ds")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="v",
            image_path="a.tif")
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume,
            z_start=0, z_end=1, y_end=8, x_end=8,
            assigned_to=self.user, status=TaskStatus.ASSIGNED)
        self.block = np.zeros((1, 8, 8), np.uint8)
        self.written = {}

    def write_slice(self, offset, mask):
        self.written[offset] = mask.copy()

    def make_plan(self, **kw):
        params = dict(block=self.block, seed=(0, 4, 4), label_id=3,
                      bbox=box(1, 8, 8))
        params.update(kw)
        return service.plan_flood_fill(**params)

    def apply(self, plan=None, **kw):
        params = dict(task=self.task, actor=self.user,
                      plan=plan or self.make_plan(),
                      write_slice=self.write_slice)
        params.update(kw)
        return service.apply_tool(**params)


@TOOLS_OFF
class ToolsDisabledTests(ServiceMixin, TestCase):
    def setUp(self):
        self.build()

    def test_disabled_by_default(self):
        self.assertFalse(service.tools_enabled())

    def test_plan_refuses(self):
        with self.assertRaises(ToolError) as c:
            self.make_plan()
        self.assertEqual(c.exception.reason, "disabled")

    def test_nothing_recorded_or_written(self):
        with self.assertRaises(ToolError):
            self.make_plan()
        self.assertEqual(AnnotationOperation.objects.count(), 0)
        self.assertEqual(self.written, {})


@TOOLS_ON
class PlanOnlyTests(ServiceMixin, TestCase):
    def setUp(self):
        self.build()

    def test_plan_mutates_nothing(self):
        plan = self.make_plan()
        self.assertEqual(plan.voxels_changed, 64)
        self.assertEqual(AnnotationOperation.objects.count(), 0)
        self.assertEqual(current_version(self.task), 0)
        self.assertEqual(self.written, {})

    def test_apply_needs_the_operations_flag(self):
        with self.assertRaises(ToolError) as c:
            self.apply()
        self.assertEqual(c.exception.reason, "operations_disabled")


@FULL_ON
class ApplyTests(ServiceMixin, TestCase):
    def setUp(self):
        self.build()

    def test_apply_records_exactly_one_operation(self):
        self.apply()
        self.assertEqual(AnnotationOperation.objects.count(), 1)
        self.assertEqual(current_version(self.task), 1)

    def test_payload_has_no_voxels_and_is_bounded(self):
        op = self.apply()
        blob = json.dumps(op.payload)
        self.assertLess(len(blob), 2048)
        self.assertNotIn("mask", blob)
        self.assertEqual(op.payload["tool"], "flood_fill")
        self.assertEqual(op.payload["bbox"], [0, 0, 0, 1, 8, 8])

    def test_idempotent_replay_returns_original_without_reapplying(self):
        a = self.apply(idempotency_key="k")
        self.written.clear()
        b = self.apply(idempotency_key="k")
        self.assertEqual(a.id, b.id)
        self.assertEqual(AnnotationOperation.objects.count(), 1)
        self.assertEqual(self.written, {})

    def test_same_key_different_parameters_rejected(self):
        self.apply(idempotency_key="k")
        other = self.make_plan(label_id=9)
        with self.assertRaises(ToolError) as c:
            self.apply(plan=other, idempotency_key="k")
        self.assertEqual(c.exception.reason, "idempotency_conflict")

    def test_stale_expected_version_conflicts(self):
        self.apply()
        with self.assertRaises(VersionConflict):
            self.apply(expected_version=0)

    def test_conflict_writes_nothing(self):
        self.apply()
        self.written.clear()
        with self.assertRaises(VersionConflict):
            self.apply(expected_version=0)
        self.assertEqual(self.written, {})
        self.assertEqual(current_version(self.task), 1)

    def test_locked_task_refused(self):
        self.task.annotation_locked = True
        self.task.save(update_fields=["annotation_locked"])
        with self.assertRaises(ToolError) as c:
            self.apply()
        self.assertEqual(c.exception.reason, "locked")

    def test_write_failure_rolls_back_the_operation(self):
        def explode(offset, mask):
            raise RuntimeError("disk went away")

        with self.assertRaises(RuntimeError):
            self.apply(write_slice=explode)
        self.assertEqual(AnnotationOperation.objects.count(), 0)
        self.assertEqual(current_version(self.task), 0)

    def test_undo_marks_the_tool_operation_undone(self):
        from annotation.operations import undo

        op = self.apply()
        inverse = undo(self.task, actor=self.user)
        op.refresh_from_db()
        self.assertIsNotNone(op.undone_at)
        self.assertEqual(inverse.inverse_of_id, op.id)

    def test_undo_does_not_restore_voxels(self):
        """Documented limit: no snapshot store exists yet (Phase 10, P0)."""
        from annotation.operations import undo

        self.apply()
        before = dict(self.written)
        undo(self.task, actor=self.user)
        self.assertEqual(self.written, before)


@requires_postgres
@FULL_ON
class ConcurrentToolTests(ServiceMixin, TransactionTestCase):
    TIMEOUT = 30

    def setUp(self):
        self.build()

    def _race(self, n, fn):
        errors, lock = [], threading.Lock()
        barrier = threading.Barrier(n, timeout=self.TIMEOUT)

        def worker(i):
            try:
                barrier.wait()
                fn(i)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=self.TIMEOUT)
        self.assertEqual([t for t in threads if t.is_alive()], [], "deadlock")
        return errors

    def test_concurrent_applies_produce_a_dense_sequence(self):
        errors = self._race(4, lambda i: service.apply_tool(
            task=self.task, actor=self.user, plan=self.make_plan(),
            write_slice=lambda o, m: None))
        self.assertEqual(errors, [])
        seqs = sorted(AnnotationOperation.objects.values_list("seq", flat=True))
        self.assertEqual(seqs, [1, 2, 3, 4])

    def test_concurrent_replay_of_one_key_applies_once(self):
        errors = self._race(4, lambda i: service.apply_tool(
            task=self.task, actor=self.user, plan=self.make_plan(),
            write_slice=lambda o, m: None, idempotency_key="same"))
        self.assertEqual(errors, [])
        self.assertEqual(AnnotationOperation.objects.count(), 1)

    def test_two_users_applying_the_same_tool(self):
        other = make_user("tool-other")
        users = [self.user, other]
        errors = self._race(4, lambda i: service.apply_tool(
            task=self.task, actor=users[i % 2], plan=self.make_plan(),
            write_slice=lambda o, m: None))
        self.assertEqual(errors, [])
        self.assertEqual(current_version(self.task), 4)
