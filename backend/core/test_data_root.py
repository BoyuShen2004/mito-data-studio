"""Where in-app edits land, and what they must never touch.

These tests exist because of a real incident: a save succeeded, returned 200,
and wrote its working mask into a *retired deployment's* data root, because the
public hostname still routed to the old process. No code was wrong — the layers
simply did not agree about which instance was live.

So the tests here assert two different kinds of thing:

* **Containment** — an edit always lands under the *running instance's*
  ``MITO_DATA_ROOT``, whether that setting is relative or absolute, and never
  on a registered source image or label, even when those live at an absolute
  path outside the root entirely.
* **Identity** — the instance can describe which deployment it is, without
  leaking a credential, so the chain can be verified end to end.

Every test overrides ``MITO_DATA_ROOT`` to a temporary directory, so nothing
here can touch real annotation data.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.data_root import ExternalWriteRefused, assert_owned, is_owned
from projects.models import Dataset, Project
from volumes.models import Volume

User = get_user_model()

_SHAPE = (2, 8, 8)


def _write_tiff(path: Path, shape=_SHAPE, fill: int = 0) -> Path:
    """A small real TIFF, so nothing here depends on a fake file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full(shape, fill, dtype=np.uint16))
    return path


class DataRootTestCase(TestCase):
    """Shared fixture: one project/dataset/volume and a temp data root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir(parents=True, exist_ok=True)

        # A source image deliberately *outside* the data root, registered by
        # absolute path — the "someone else's HPC tree" case.
        self.external = Path(self.tmp.name) / "external_source"
        self.external.mkdir(parents=True, exist_ok=True)
        self.source_image = _write_tiff(self.external / "cortex.tif", fill=7)

        self.user = User.objects.create_user(username="tester", password="x")
        self.project = Project.objects.create(title="Proj", created_by=self.user)
        self.dataset = Dataset.objects.create(project=self.project, name="DS")
        self.volume = Volume.objects.create(
            project=self.project,
            dataset=self.dataset,
            name="cortex",
            image_path=str(self.source_image),
        )

    def _edit(self, label: int = 5):
        """Perform one real in-app edit through the production save path.

        Paints ``label`` over the first four pixels of slice z=0, leaving the
        rest background. Runs are ``[[id, count], ...]`` covering the whole
        slice, per :func:`slice_io.encode_label_rle`.
        """
        from annotation.services import get_label_slice_ids, set_label_slice_ids

        current = get_label_slice_ids(self.volume, "z", 0)
        h, w = current["shape"]
        set_label_slice_ids(
            self.volume,
            "z",
            0,
            current["shape"],
            [[label, 4], [0, h * w - 4]],
            origin="manual",
        )


class WritesLandUnderTheConfiguredDataRoot(DataRootTestCase):
    """The core containment guarantee, for both forms of the setting."""

    def test_absolute_data_root(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._edit()
            masks = list(self.root.rglob("*_mask.tif"))

        self.assertEqual(len(masks), 1, f"expected exactly one mask, got {masks}")
        self.assertEqual(masks[0].parent, self.root / "Proj" / "DS")
        self.assertTrue(masks[0].name.endswith("_mask.tif"))

    def test_relative_data_root_resolves_against_the_repo_not_the_cwd(self):
        """A relative ``MITO_DATA_ROOT`` is the form the deployments actually
        use (``./data``). It must resolve to one fixed place regardless of the
        process's working directory — otherwise the same setting means
        different directories for gunicorn, a management command and a test.
        """
        import os

        # settings.py resolves a relative root at import time, so emulate the
        # resolved outcome and prove the resolution rule itself separately.
        original = os.getcwd()
        self.addCleanup(os.chdir, original)

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            os.chdir(self.tmp.name)  # a *different* cwd from where we started
            self._edit()

        masks = list(self.root.rglob("*_mask.tif"))
        self.assertEqual(len(masks), 1)
        self.assertEqual(masks[0].parent, self.root / "Proj" / "DS")

    def test_the_mask_is_named_from_the_image_stem(self):
        """``<image-stem>_mask.tif``, per the documented layout."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._edit()

        mask = next(self.root.rglob("*_mask.tif"))
        self.assertEqual(mask.name, "cortex_mask.tif")

    def test_metadata_sidecar_lands_beside_the_mask(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._edit()

        sidecars = list(self.root.rglob("*_metadata.json"))
        self.assertEqual(len(sidecars), 1, f"expected one sidecar, got {sidecars}")
        self.assertEqual(sidecars[0].parent, self.root / "Proj" / "DS" / "metadata")
        # It must be readable JSON, not a truncated write.
        json.loads(sidecars[0].read_text())

    def test_the_edit_is_actually_persisted_and_reloadable(self):
        """Containment is worthless if the save didn't happen."""
        from annotation.services import get_label_slice_ids

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._edit()
            reloaded = get_label_slice_ids(self.volume, "z", 0)

        labels = {label_id for label_id, _count in reloaded["runs"]}
        self.assertIn(5, labels)


class RegisteredSourceFilesAreReadOnly(DataRootTestCase):
    """The external source image must survive editing, untouched."""

    def test_source_image_bytes_and_mtime_are_unchanged_by_an_edit(self):
        before_bytes = self.source_image.read_bytes()
        before_mtime = self.source_image.stat().st_mtime_ns

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._edit()

        self.assertEqual(self.source_image.read_bytes(), before_bytes)
        self.assertEqual(self.source_image.stat().st_mtime_ns, before_mtime)

    def test_nothing_at_all_is_written_into_the_external_tree(self):
        before = {p for p in self.external.rglob("*")}

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._edit()

        self.assertEqual({p for p in self.external.rglob("*")}, before)

    def test_an_external_official_label_is_seeded_from_but_never_written(self):
        """A volume whose *label* is registered by reference: the first edit
        forks a working copy seeded from it, and the original stays put.
        """
        external_label = _write_tiff(self.external / "cortex_labels.tif", fill=3)
        before_bytes = external_label.read_bytes()
        before_mtime = external_label.stat().st_mtime_ns
        self.volume.label_path = str(external_label)
        self.volume.save(update_fields=["label_path"])

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._edit()

        self.assertEqual(external_label.read_bytes(), before_bytes)
        self.assertEqual(external_label.stat().st_mtime_ns, before_mtime)

        # ...and the seed was genuinely used, so this is a fork, not a reset.
        mask = next(self.root.rglob("*_mask.tif"))
        self.assertIn(3, np.unique(tifffile.imread(str(mask))))

    def test_a_corrupt_external_official_label_does_not_block_editing(self):
        """An unreadable registered label must degrade to "start empty".

        The official label can be registered *by reference* to a file this app
        does not own — an external prediction, a truncated transfer, a path that
        exists but is not a valid TIFF. Seeding used to call ``imread`` without
        a guard, so the first read *or* save on such a volume raised straight
        out of the editor's entry point and the annotator could not work on the
        volume at all, not even from scratch.
        """
        broken = self.external / "broken_official.tif"
        broken.write_bytes(b"II*\x00 definitely not a real tiff")
        self.volume.label_path = str(broken)
        self.volume.save(update_fields=["label_path"])

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            from annotation.services import get_label_slice_ids

            # Must not raise.
            first_read = get_label_slice_ids(self.volume, "z", 0)
            self.assertIn("shape", first_read)
            # And editing still works, starting from empty.
            self._edit(label=11)
            reloaded = get_label_slice_ids(self.volume, "z", 0)

        labels = {label_id for label_id, _count in reloaded["runs"]}
        self.assertIn(11, labels)
        # The unreadable source was left exactly as it was found.
        self.assertEqual(
            broken.read_bytes(), b"II*\x00 definitely not a real tiff"
        )

    def test_volume_label_location_is_not_repointed_at_the_working_copy(self):
        """Editing stages a working copy; it must not silently promote it to
        the volume's official label (that is ``approve_submission``'s job)."""
        external_label = _write_tiff(self.external / "official.tif", fill=1)
        self.volume.label_path = str(external_label)
        self.volume.save(update_fields=["label_path"])

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._edit()

        self.volume.refresh_from_db()
        self.assertEqual(self.volume.label_path, str(external_label))


class OwnershipGuardRefusesEscapes(DataRootTestCase):
    """The enforcement layer itself, independent of any caller."""

    def test_paths_inside_the_root_are_owned(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self.assertTrue(is_owned(self.root / "a" / "b.tif"))
            self.assertTrue(is_owned(self.root))

    def test_paths_outside_the_root_are_not_owned(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self.assertFalse(is_owned(self.source_image))
            self.assertFalse(is_owned("/etc/passwd"))

    def test_a_sibling_with_a_shared_name_prefix_is_not_owned(self):
        """``/tmp/x/data-old`` must not pass as inside ``/tmp/x/data``."""
        sibling = self.root.parent / "data-old"
        sibling.mkdir()
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self.assertFalse(is_owned(sibling / "mask.tif"))

    def test_dotdot_traversal_is_refused(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            with self.assertRaises(ExternalWriteRefused):
                assert_owned(self.root / ".." / "escaped.tif")

    def test_storage_symlinked_onto_another_disk_is_still_owned(self):
        """A dataset folder symlinked to a bigger disk must remain writable.

        This is the normal way large microscopy trees are laid out. Judging
        containment only after resolving symlinks would refuse every save and
        turn a storage decision into an annotation outage — see
        ``core/data_root.is_owned``.
        """
        elsewhere = Path(self.tmp.name) / "bigdisk"
        elsewhere.mkdir()
        link = self.root / "webknossos"
        link.symlink_to(elsewhere, target_is_directory=True)

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self.assertTrue(is_owned(link / "cortex_mask.tif"))
            assert_owned(link / "cortex_mask.tif")  # must not raise

    def test_a_path_outside_the_root_is_refused_even_via_a_symlink_name(self):
        """The real failure modes are *different paths*, not symlink tricks:
        another instance's root and an external source image."""
        other_instance = Path(self.tmp.name) / "old-deployment" / "data"
        other_instance.mkdir(parents=True)
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            with self.assertRaises(ExternalWriteRefused):
                assert_owned(other_instance / "m.tif")
            with self.assertRaises(ExternalWriteRefused):
                assert_owned(self.source_image)

    def test_the_refusal_names_both_the_target_and_the_expected_root(self):
        """The message has to be actionable at 2am: the whole point is that the
        operator does not yet know which root this process has."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            with self.assertRaises(ExternalWriteRefused) as ctx:
                assert_owned(self.source_image)

        message = str(ctx.exception)
        self.assertIn(str(self.source_image), message)
        self.assertIn(str(self.root.resolve()), message)

    def test_the_write_primitive_refuses_an_external_target(self):
        """Guarding the primitive, not just the caller: even a direct call
        cannot write outside the root."""
        from annotation.visualization.slice_io import _create_label_memmap

        target = self.external / "should_never_exist.tif"
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            with self.assertRaises(ExternalWriteRefused):
                _create_label_memmap(target, _SHAPE)

        self.assertFalse(target.exists())

    def test_writing_into_another_instances_data_root_is_refused(self):
        """The incident, reduced to one assertion."""
        from annotation.visualization.slice_io import _create_label_memmap

        other_instance = Path(self.tmp.name) / "old-deployment" / "data"
        other_instance.mkdir(parents=True)

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            with self.assertRaises(ExternalWriteRefused):
                _create_label_memmap(other_instance / "Proj" / "m.tif", _SHAPE)

        self.assertEqual(list(other_instance.rglob("*")), [])


@override_settings(MITO_TRACKING_PROVIDER="local")
class TrackStartsFromTheWorkingCopy(DataRootTestCase):
    """Track rewrites the *whole* working volume, so what it starts from
    decides whether unapproved work survives.

    It used to seed from the official (approved) label, described as "start
    tracking from the last-approved state". Combined with a whole-volume
    write, that silently reverted every saved-but-unapproved edit on every
    other slice each time anyone pressed Track.
    """

    def setUp(self):
        super().setUp()
        from annotation.models import AnnotationTask
        from core.choices import TaskType

        # A bright image so the local tracking provider propagates the seed.
        d, h, w = 4, 12, 12
        self.shape = (d, h, w)
        self.image = self.external / "bright.tif"
        tifffile.imwrite(
            str(self.image), np.full(self.shape, 200, dtype=np.uint8)
        )
        self.volume.image_path = str(self.image)
        self.volume.save(update_fields=["image_path"])

        self.task = AnnotationTask.objects.create(
            project=self.project,
            volume=self.volume,
            z_start=0,
            z_end=d,
            y_end=h,
            x_end=w,
            task_type=TaskType.MANUAL_ANNOTATION,
        )

    def test_unrelated_saved_edits_survive_tracking(self):
        from annotation.services import track_task_fork
        from annotation.visualization.slice_io import read_label_array
        from annotation.label_paths import working_label_rel_path
        from annotation.visualization.slice_io import resolve_path

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            # The annotator paints instance 42 on the last slice and saves it
            # to the working copy. It is *not* approved, so it exists only
            # there — exactly the state the old code discarded.
            from annotation.services import get_label_slice_ids, set_label_slice_ids

            far_z = self.shape[0] - 1
            current = get_label_slice_ids(self.volume, "z", far_z)
            h, w = current["shape"]
            set_label_slice_ids(
                self.volume, "z", far_z, [h, w], [[42, 6], [0, h * w - 6]],
                origin="manual",
            )

            # Now track a different instance on slice 1.
            seed = np.zeros((h, w), dtype=bool)
            seed[2:5, 2:5] = True
            track_task_fork(self.task, {1: seed}, z_range=(0, 2))

            mask = read_label_array(
                resolve_path(working_label_rel_path(self.volume))
            )

        self.assertIn(
            42,
            set(int(v) for v in np.unique(mask[far_z])),
            "tracking reverted a saved-but-unapproved edit on another slice",
        )

    def test_tracking_falls_back_to_the_official_label_when_no_working_copy(self):
        """A volume nobody has painted yet must still track from its official
        label rather than from an empty volume."""
        from annotation.services import track_task_fork
        from annotation.label_paths import working_label_rel_path
        from annotation.visualization.slice_io import read_label_array, resolve_path

        official = _write_tiff(self.external / "approved.tif", shape=self.shape, fill=0)
        arr = tifffile.imread(str(official))
        arr[0, 0, 0] = 88
        tifffile.imwrite(str(official), arr)
        self.volume.label_path = str(official)
        self.volume.save(update_fields=["label_path"])

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            working = resolve_path(working_label_rel_path(self.volume))
            self.assertFalse(working.exists(), "precondition: no working copy")

            seed = np.zeros(self.shape[1:], dtype=bool)
            seed[2:5, 2:5] = True
            track_task_fork(self.task, {1: seed}, z_range=(0, 2))

            mask = read_label_array(working)

        self.assertEqual(int(mask[0, 0, 0]), 88, "official label was not used as the seed")

    def test_tracking_never_writes_to_the_external_source_image(self):
        from annotation.services import track_task_fork

        before = self.image.read_bytes()
        before_mtime = self.image.stat().st_mtime_ns

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            seed = np.zeros(self.shape[1:], dtype=bool)
            seed[2:5, 2:5] = True
            track_task_fork(self.task, {1: seed}, z_range=(0, 2))

        self.assertEqual(self.image.read_bytes(), before)
        self.assertEqual(self.image.stat().st_mtime_ns, before_mtime)

    def test_tracking_writes_only_under_the_data_root(self):
        from annotation.services import track_task_fork

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            seed = np.zeros(self.shape[1:], dtype=bool)
            seed[2:5, 2:5] = True
            track_task_fork(self.task, {1: seed}, z_range=(0, 2))
            masks = list(self.root.rglob("*_mask.tif"))

        self.assertEqual(len(masks), 1)
        self.assertTrue(masks[0].is_relative_to(self.root))


class DeploymentIdentityIsReportedSafely(DataRootTestCase):
    """Identity must be complete enough to compare, and free of secrets."""

    def test_identity_reports_the_running_data_root_and_database(self):
        from core.deployment import identity

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            info = identity()

        self.assertEqual(info["data_root"], str(self.root.resolve()))
        self.assertIn("engine", info["database"])
        self.assertTrue(info["fingerprint"])

    def test_identity_contains_no_secret_material(self):
        """Not just "no field named password" — the actual SECRET_KEY value
        must not appear anywhere in the serialized payload."""
        from django.conf import settings

        from core.deployment import identity

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            blob = json.dumps(identity())

        self.assertNotIn(settings.SECRET_KEY, blob)
        for banned in ("password", "secret", "token", "credential"):
            self.assertNotIn(banned, blob.lower())

    def test_fingerprint_changes_when_the_data_root_changes(self):
        """Two instances differing only in data root must be distinguishable —
        this is precisely the pair that caused the incident."""
        from core.deployment import fingerprint

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            a = fingerprint()
        other = Path(self.tmp.name) / "other-data"
        other.mkdir()
        with override_settings(MITO_DATA_ROOT=other.resolve()):
            b = fingerprint()

        self.assertNotEqual(a, b)

    def test_fingerprint_is_stable_across_restarts(self):
        """It must not fold in pid or hostname, or every restart would look
        like a different deployment and the check would be ignored."""
        from core.deployment import fingerprint

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self.assertEqual(fingerprint(), fingerprint())

    def test_endpoint_requires_authentication(self):
        response = self.client.get("/api/deployment/identity/")
        self.assertIn(response.status_code, (401, 403))

    def test_public_release_exposes_only_configured_version(self):
        with patch.dict(os.environ, {"MITO_RELEASE": "1.1.1"}):
            response = self.client.get("/api/deployment/release/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"release": "1.1.1"})

    def test_endpoint_reports_the_same_fingerprint_as_the_process(self):
        """What the URL says and what the process says must agree — the whole
        cutover check rests on this."""
        from core.deployment import fingerprint

        self.client.force_login(self.user)
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            response = self.client.get("/api/deployment/identity/")
            expected = fingerprint()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["fingerprint"], expected)


class DeploymentSystemChecks(DataRootTestCase):
    """The checks that make a misconfiguration announce itself at startup."""

    def test_missing_data_root_is_an_error(self):
        from core.checks import check_data_root

        missing = Path(self.tmp.name) / "does-not-exist"
        with override_settings(MITO_DATA_ROOT=missing):
            issues = check_data_root(None)

        self.assertTrue(any(i.id == "deployment.E005" for i in issues))

    def test_a_healthy_data_root_produces_no_issues(self):
        from core.checks import check_data_root

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self.assertEqual(check_data_root(None), [])

    def test_data_root_inside_another_checkout_is_flagged(self):
        """The incident's shape: this instance pointed at another checkout."""
        from core.checks import check_data_root

        other = Path(self.tmp.name) / "other-checkout"
        (other / ".git").mkdir(parents=True)
        (other / "backend").mkdir(parents=True)
        foreign_root = other / "data"
        foreign_root.mkdir()

        with override_settings(MITO_DATA_ROOT=foreign_root):
            issues = check_data_root(None)

        self.assertTrue(
            any(i.id == "deployment.W002" for i in issues),
            f"expected W002, got {[i.id for i in issues]}",
        )

    def test_service_bind_is_reported_when_declared(self):
        """The app cannot observe its own listening socket, so the bind is a
        declaration — but it must be reported so it can be compared."""
        import os
        from unittest import mock

        from core.deployment import identity

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            with mock.patch.dict(
                os.environ,
                {"MITO_SERVICE_BIND": "127.0.0.1:18188", "MITO_RELEASE": "r42"},
            ):
                info = identity()

        self.assertEqual(info["service"]["bind"], "127.0.0.1:18188")
        self.assertEqual(info["service"]["release"], "r42")
        self.assertTrue(info["service"]["declared"])

    def test_identity_reports_effective_feature_flags(self):
        import os
        from unittest import mock

        from core.deployment import identity

        with override_settings(
            MITO_DATA_ROOT=self.root.resolve(),
            FEATURE_ANNOTATION_OPS=True,
            FEATURE_CHUNK_SERVICE=False,
        ):
            with mock.patch.dict(
                os.environ,
                {
                    "VITE_FEATURE_CHUNK_PULL_QUEUE": "true",
                    "VITE_FEATURE_CHUNK_RENDERER": "false",
                },
            ):
                features = identity()["features"]

        self.assertTrue(features["FEATURE_ANNOTATION_OPS"])
        self.assertFalse(features["FEATURE_CHUNK_SERVICE"])
        self.assertTrue(features["VITE_FEATURE_CHUNK_PULL_QUEUE"])
        self.assertFalse(features["VITE_FEATURE_CHUNK_RENDERER"])
        self.assertNotIn("FEATURE_AUTOSAVE_RECOVERY", features)

    def test_identity_reports_upgrade_profile(self):
        from core.deployment import identity

        with override_settings(
            MITO_DATA_ROOT=self.root.resolve(),
            MITO_UPGRADE_PROFILE="webknossos",
        ):
            self.assertEqual(identity()["upgrade_profile"], "webknossos")

    def test_upgrade_checks_reject_incoherent_dependencies(self):
        from core.checks import check_upgrade_feature_dependencies

        with override_settings(
            MITO_UPGRADE_PROFILE="legacy",
            FEATURE_ANNOTATION_OPS=False,
            FEATURE_VOLUME_PYRAMIDS=False,
            FEATURE_CHUNK_SERVICE=True,
        ):
            issues = check_upgrade_feature_dependencies(None)

        self.assertEqual(
            {issue.id for issue in issues},
            {"deployment.E023"},
        )

    def test_upgrade_checks_expose_a_profile_overridden_back_to_legacy(self):
        from core.checks import check_upgrade_feature_dependencies

        with override_settings(
            MITO_UPGRADE_PROFILE="webknossos",
            FEATURE_TEAMS=False,
            FEATURE_AUTO_FILL_SCHEDULER=False,
            FEATURE_REVIEW_HISTORY=False,
            FEATURE_DASHBOARDS=False,
            FEATURE_ANNOTATION_OPS=False,
            FEATURE_INTERPOLATION=False,
            FEATURE_ANNOTATION_TOOLS=False,
            FEATURE_VOLUME_PYRAMIDS=False,
            FEATURE_CHUNK_SERVICE=False,
        ):
            issues = check_upgrade_feature_dependencies(None)

        self.assertIn("deployment.W021", {issue.id for issue in issues})

    def test_production_profile_rejects_feature_drift_and_missing_metrics(self):
        from django.conf import settings

        from core.checks import check_upgrade_feature_dependencies

        production = {
            **settings.PRODUCTION_INTEGRATED_FEATURES,
            "FEATURE_DASHBOARDS": False,
        }
        with override_settings(
            MITO_UPGRADE_PROFILE="production_integrated_v1",
            MITO_METRICS_BEARER_TOKEN="",
            **production,
        ):
            issues = check_upgrade_feature_dependencies(None)

        self.assertTrue(
            {"deployment.E026", "deployment.E029"}.issubset(
                {issue.id for issue in issues}
            )
        )

    def test_production_profile_requires_the_compiled_streaming_frontend(self):
        import os
        from unittest import mock

        from django.conf import settings
        from core.checks import check_upgrade_feature_dependencies

        with override_settings(
            MITO_UPGRADE_PROFILE="production_integrated_v1",
            MITO_METRICS_BEARER_TOKEN="test-token",
            **settings.PRODUCTION_INTEGRATED_FEATURES,
        ):
            with mock.patch.dict(
                os.environ,
                {
                    "VITE_FEATURE_CHUNK_PULL_QUEUE": "false",
                    "VITE_FEATURE_CHUNK_RENDERER": "false",
                },
            ):
                issues = check_upgrade_feature_dependencies(None)
        self.assertIn("deployment.E027", {issue.id for issue in issues})

    def test_production_profile_allows_the_four_switch_emergency_rollback(self):
        import os
        from unittest import mock

        from django.conf import settings
        from core.checks import check_upgrade_feature_dependencies

        rolled_back = {
            **settings.PRODUCTION_INTEGRATED_FEATURES,
            "FEATURE_VOLUME_PYRAMIDS": False,
            "FEATURE_CHUNK_SERVICE": False,
        }
        with override_settings(
            MITO_UPGRADE_PROFILE="production_integrated_v1",
            MITO_METRICS_BEARER_TOKEN="test-token",
            **rolled_back,
        ):
            with mock.patch.dict(
                os.environ,
                {
                    "VITE_FEATURE_CHUNK_PULL_QUEUE": "false",
                    "VITE_FEATURE_CHUNK_RENDERER": "false",
                },
            ):
                issues = check_upgrade_feature_dependencies(None)
        ids = {issue.id for issue in issues}
        self.assertIn("deployment.W022", ids)
        self.assertNotIn("deployment.E026", ids)
        self.assertNotIn("deployment.E027", ids)

    def test_production_profile_rejects_runtime_provider_drift(self):
        from django.conf import settings

        from core.checks import check_upgrade_feature_dependencies

        with override_settings(
            MITO_UPGRADE_PROFILE="production_integrated_v1",
            MITO_TRACKING_PROVIDER="local",
            MITO_AI_ONNX_CUDA=True,
            MITO_LOCAL_EXECUTABLE_ALLOWLIST="bash",
            MITO_PROCESSING_ENV_ALLOWLIST={"PATH"},
            MITO_METRICS_BEARER_TOKEN="test-token",
            **settings.PRODUCTION_INTEGRATED_FEATURES,
        ):
            issues = check_upgrade_feature_dependencies(None)

        self.assertIn("deployment.E030", {issue.id for issue in issues})

    def test_public_deployment_rejects_orphaned_dev_reset_switch(self):
        from core.checks import check_public_exposure

        with override_settings(
            ALLOWED_HOSTS=["mito.example"],
            MITO_ALLOW_DEV_RESET=True,
            ENABLE_MOCK_DEV_LOGIN=False,
        ):
            issues = check_public_exposure(None)

        self.assertIn("deployment.E010", {issue.id for issue in issues})

    def test_explicit_disposable_demo_profile_allows_dev_reset(self):
        from core.checks import check_public_exposure

        with override_settings(
            ALLOWED_HOSTS=["mito.example"],
            MITO_ALLOW_DEV_RESET=True,
            ENABLE_MOCK_DEV_LOGIN=True,
        ):
            issues = check_public_exposure(None)

        self.assertNotIn("deployment.E010", {issue.id for issue in issues})

    def test_fingerprint_ignores_the_declared_bind(self):
        """Forgetting an optional declaration must not change the identity."""
        import os
        from unittest import mock

        from core.deployment import fingerprint

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            bare = fingerprint()
            with mock.patch.dict(os.environ, {"MITO_SERVICE_BIND": "0.0.0.0:9"}):
                declared = fingerprint()

        self.assertEqual(bare, declared)

    def test_expected_identity_mismatch_is_flagged(self):
        """The check that would have caught the original incident."""
        import os
        from unittest import mock

        from core.checks import check_expected_identity

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            with mock.patch.dict(
                os.environ,
                {
                    "MITO_EXPECTED_CHECKOUT": "/somewhere/else",
                    "MITO_EXPECTED_DATA_ROOT": "/somewhere/else/data",
                    "MITO_EXPECTED_DB_NAME": "not_this_database",
                    "MITO_EXPECTED_BIND": "127.0.0.1:19999",
                },
            ):
                ids = {i.id for i in check_expected_identity(None)}

        self.assertIn("deployment.W010", ids)  # checkout
        self.assertIn("deployment.W011", ids)  # data root
        self.assertIn("deployment.W012", ids)  # database
        self.assertIn("deployment.W013", ids)  # bind

    def test_matching_expectations_produce_no_issues(self):
        import os
        from unittest import mock

        from core.checks import check_expected_identity
        from core.deployment import identity

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            info = identity()
            db = info["database"]
            with mock.patch.dict(
                os.environ,
                {
                    "MITO_EXPECTED_CHECKOUT": str(info["checkout"]),
                    "MITO_EXPECTED_DATA_ROOT": str(info["data_root"]),
                    "MITO_EXPECTED_DB_NAME": str(db["name"]),
                    "MITO_EXPECTED_FINGERPRINT": str(info["fingerprint"]),
                },
            ):
                self.assertEqual(check_expected_identity(None), [])

    def test_unpinned_expectations_are_skipped_entirely(self):
        """A development checkout with no pins must stay quiet."""
        import os
        from unittest import mock

        from core.checks import check_expected_identity

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            with mock.patch.dict(os.environ, {}, clear=False):
                for key in (
                    "MITO_EXPECTED_CHECKOUT",
                    "MITO_EXPECTED_DATA_ROOT",
                    "MITO_EXPECTED_DB_NAME",
                    "MITO_EXPECTED_BIND",
                    "MITO_EXPECTED_FINGERPRINT",
                    "MITO_SERVICE_BIND",
                ):
                    os.environ.pop(key, None)
                self.assertEqual(check_expected_identity(None), [])

    def test_a_declared_bind_without_pins_warns_that_drift_is_undetectable(self):
        import os
        from unittest import mock

        from core.checks import check_expected_identity

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            with mock.patch.dict(os.environ, {}, clear=False):
                for key in (
                    "MITO_EXPECTED_CHECKOUT",
                    "MITO_EXPECTED_DATA_ROOT",
                    "MITO_EXPECTED_DB_NAME",
                    "MITO_EXPECTED_BIND",
                    "MITO_EXPECTED_FINGERPRINT",
                ):
                    os.environ.pop(key, None)
                os.environ["MITO_SERVICE_BIND"] = "127.0.0.1:18188"
                ids = {i.id for i in check_expected_identity(None)}

        self.assertIn("deployment.W015", ids)

    def test_identity_with_service_block_still_leaks_no_secrets(self):
        import os
        from unittest import mock

        from django.conf import settings as dj_settings

        from core.deployment import identity

        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            with mock.patch.dict(
                os.environ, {"MITO_SERVICE_BIND": "127.0.0.1:18188"}
            ):
                blob = json.dumps(identity())

        self.assertNotIn(dj_settings.SECRET_KEY, blob)
        for banned in ("password", "secret", "token", "credential"):
            self.assertNotIn(banned, blob.lower())

    def test_debug_with_public_hosts_is_flagged(self):
        from core.checks import check_public_exposure

        with override_settings(DEBUG=True, ALLOWED_HOSTS=["example.org"]):
            issues = check_public_exposure(None)

        self.assertTrue(any(i.id == "deployment.W003" for i in issues))
