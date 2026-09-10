import os
import tempfile
from pathlib import Path

from django.test import TestCase, TransactionTestCase, override_settings

from projects.services import (
    DeleteBlocked,
    create_project,
    delete_dataset,
    delete_project,
    delete_volume,
    get_or_create_dataset,
    update_dataset,
)


class DatasetHierarchyTests(TestCase):
    """project → dataset → volume, and the guards around deleting them."""

    def setUp(self):
        self.project = create_project(title="Study")

    def _dataset(self, name="CellMap"):
        return get_or_create_dataset(project=self.project, name=name)

    def _volume(self, dataset, name="case_00"):
        from volumes.services import register_volume

        return register_volume(
            dataset=dataset, name=name, image_path=f"{name}.tif", autodetect_shape=False
        )

    def test_project_holds_many_datasets(self):
        a = self._dataset("CellMap")
        b = self._dataset("MitoEM")
        self.assertEqual(self.project.datasets.count(), 2)
        self.assertNotEqual(a.id, b.id)

    def test_dataset_holds_many_volumes(self):
        ds = self._dataset()
        self._volume(ds, "case_00")
        self._volume(ds, "case_01")
        self.assertEqual(ds.volumes.count(), 2)

    def test_volume_project_is_taken_from_its_dataset(self):
        ds = self._dataset()
        volume = self._volume(ds)
        # The denormalised FK must agree with the dataset's project.
        self.assertEqual(volume.project_id, self.project.id)
        self.assertEqual(volume.dataset_id, ds.id)

    def test_same_name_reuses_the_dataset(self):
        first = self._dataset("CellMap")
        again = get_or_create_dataset(
            project=self.project, name="CellMap", metadata={"organism": "mouse"}
        )
        self.assertEqual(first.id, again.id)
        self.assertEqual(self.project.datasets.count(), 1)
        # Metadata merges rather than replacing.
        self.assertEqual(again.metadata["organism"], "mouse")

    def test_same_name_in_another_project_is_a_different_dataset(self):
        other = create_project(title="Other")
        a = self._dataset("CellMap")
        b = get_or_create_dataset(project=other, name="CellMap")
        self.assertNotEqual(a.id, b.id)

    def test_update_dataset_renames_and_merges_metadata(self):
        ds = get_or_create_dataset(
            project=self.project, name="Old", metadata={"organism": "mouse"}
        )
        update_dataset(ds, name="New", metadata={"tissue": "kidney"})
        ds.refresh_from_db()
        self.assertEqual(ds.name, "New")
        self.assertEqual(ds.metadata, {"organism": "mouse", "tissue": "kidney"})

    def test_moving_a_dataset_moves_its_volumes(self):
        ds = self._dataset()
        volume = self._volume(ds)
        target = create_project(title="Target")

        update_dataset(ds, project=target)

        volume.refresh_from_db()
        self.assertEqual(volume.project_id, target.id)

    def test_delete_dataset_without_work_succeeds(self):
        ds = self._dataset()
        self._volume(ds)
        counts = delete_dataset(ds)
        self.assertEqual(counts["volumes"], 1)
        self.assertEqual(self.project.datasets.count(), 0)

    def test_delete_is_blocked_while_tasks_exist(self):
        from annotation.models import AnnotationTask

        ds = self._dataset()
        volume = self._volume(ds)
        AnnotationTask.objects.create(
            project=self.project, volume=volume, z_start=0, z_end=8,
            y_start=0, y_end=8, x_start=0, x_end=8, task_type="manual_annotation",
        )
        with self.assertRaises(DeleteBlocked) as ctx:
            delete_dataset(ds)
        self.assertEqual(ctx.exception.counts["tasks"], 1)
        # Nothing was removed.
        self.assertEqual(self.project.datasets.count(), 1)

    def test_forced_delete_removes_the_work(self):
        from annotation.models import AnnotationTask

        ds = self._dataset()
        volume = self._volume(ds)
        AnnotationTask.objects.create(
            project=self.project, volume=volume, z_start=0, z_end=8,
            y_start=0, y_end=8, x_start=0, x_end=8, task_type="manual_annotation",
        )
        counts = delete_dataset(ds, force=True)
        self.assertEqual(counts["tasks"], 1)
        self.assertEqual(AnnotationTask.objects.count(), 0)

    def test_deleting_a_project_reports_its_datasets(self):
        self._dataset("A")
        self._dataset("B")
        counts = delete_project(self.project)
        self.assertEqual(counts["datasets"], 2)


class DeleteRemovesGeneratedFilesTests(TestCase):
    """A delete also removes the files the app generated for what it deleted —
    and nothing a registered source or a surviving volume still needs."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        override = override_settings(MITO_DATA_ROOT=str(self.root))
        override.enable()
        self.addCleanup(override.disable)
        self.project = create_project(title="Study")
        self.dataset = get_or_create_dataset(project=self.project, name="CellMap")

    def _volume(self, name, **extra):
        from volumes.services import register_volume

        return register_volume(
            dataset=self.dataset, name=name, image_path=f"{name}.tif",
            autodetect_shape=False, enqueue_pyramid=False, **extra,
        )

    def _touch(self, rel):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path

    def _generate(self, volume):
        """Write each kind of file the app generates for ``volume``."""
        from annotation.label_paths import (
            volume_embeddings_dir_rel_path,
            working_label_metadata_rel_path,
            working_label_rel_path,
            working_mask_stem,
        )
        from volumes.pyramid.store import LAYER_REGION, pyramid_rel_path

        stem = working_mask_stem(volume)
        working = working_label_rel_path(volume)
        embeddings = volume_embeddings_dir_rel_path(volume)
        return [
            self._touch(working),
            self._touch(f"{working}.write.lock"),
            self._touch(working_label_metadata_rel_path(volume)),
            self._touch(f"{embeddings}/sam2-hiera-l/{stem}_z_0_1700000000.npz"),
            self._touch(f"{embeddings}/vits/{stem}_z_3_1700000000.npy"),
            self._touch(f"{pyramid_rel_path(volume)}/zarr.json"),
            self._touch(f"{pyramid_rel_path(volume, LAYER_REGION)}/zarr.json"),
        ]

    def _task(self, volume):
        from annotation.models import AnnotationTask

        return AnnotationTask.objects.create(
            project=self.project, volume=volume, z_start=0, z_end=8,
            y_start=0, y_end=8, x_start=0, x_end=8, task_type="manual_annotation",
        )

    def test_deleting_a_volume_removes_only_its_own_generated_files(self):
        doomed, kept = self._volume("case_00"), self._volume("case_01")
        doomed_files, kept_files = self._generate(doomed), self._generate(kept)
        upload = self._touch(f"submissions/task_{self._task(doomed).pk}/label.tif")

        with self.captureOnCommitCallbacks(execute=True):
            delete_volume(doomed, force=True)

        for path in [*doomed_files, upload]:
            self.assertFalse(path.exists(), path)
        for path in kept_files:
            self.assertTrue(path.exists(), path)

    def test_a_registered_label_inside_the_data_root_is_kept(self):
        from annotation.label_paths import working_label_rel_path

        volume = self._volume(
            "case_02", label_path="Study/CellMap/case_02_mask.tif", label_type="partial",
        )
        # The registered label sits exactly where the working mask would.
        self.assertEqual(working_label_rel_path(volume), volume.label_path)
        source = self._touch(volume.label_path)

        with self.captureOnCommitCallbacks(execute=True):
            delete_volume(volume)

        self.assertTrue(source.exists())

    def test_deleting_a_dataset_removes_its_folder_and_keeps_the_project_folder(self):
        self._generate(self._volume("case_00"))
        self._touch("Study/CellMap/notes.txt")
        self._touch("Study/CellMap/embeddings/sam2-hiera-l/retired_z_0_1.npz")

        with self.captureOnCommitCallbacks(execute=True):
            delete_dataset(self.dataset)

        self.assertFalse((self.root / "Study" / "CellMap").exists())
        # The project still exists, so its folder stays.
        self.assertTrue((self.root / "Study").is_dir())

    def test_a_deleted_dataset_keeps_only_its_registered_sources(self):
        registered = "Study/CellMap/labels/case_00_label.tif"
        self._generate(self._volume("case_00", label_path=registered, label_type="partial"))
        source = self._touch(registered)
        self._touch("Study/CellMap/notes.txt")

        with self.captureOnCommitCallbacks(execute=True):
            delete_dataset(self.dataset)

        self.assertTrue(source.exists())
        left = sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*") if path.is_file()
        )
        self.assertEqual(left, [registered])

    def test_deleting_the_last_volume_empties_but_keeps_the_dataset_folder(self):
        volume = self._volume("case_00")
        self._generate(volume)

        with self.captureOnCommitCallbacks(execute=True):
            delete_volume(volume)

        # Dataset and project still exist: their folders stay, with nothing left
        # inside — emptied artifact subfolders go too.
        self.assertEqual(list((self.root / "Study" / "CellMap").iterdir()), [])

    def test_deleting_a_project_removes_its_folder(self):
        self._generate(self._volume("case_00"))

        with self.captureOnCommitCallbacks(execute=True):
            delete_project(self.project)

        self.assertFalse((self.root / "Study").exists())


class DeleteThroughTheApiTests(TransactionTestCase):
    """UI delete → API → service → disk, end to end.

    A ``TransactionTestCase``, so nothing holds ``on_commit`` back: the delete
    commits and the cleanup runs exactly as in a production request. File names
    are written literally, the way production stores them, rather than derived
    from the helpers under test — a naming mismatch must fail here.
    """

    MTIME = 1788927985

    def setUp(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        root_tmp, external_tmp = tempfile.TemporaryDirectory(), tempfile.TemporaryDirectory()
        self.addCleanup(root_tmp.cleanup)
        self.addCleanup(external_tmp.cleanup)
        self.root = Path(root_tmp.name)
        self.external = Path(external_tmp.name)
        override = override_settings(MITO_DATA_ROOT=str(self.root))
        override.enable()
        self.addCleanup(override.disable)

        self.api = APIClient()
        self.api.force_authenticate(get_user_model().objects.create_superuser("mgr", password="x"))
        self.project = create_project(title="mds validation")
        self.dataset = get_or_create_dataset(project=self.project, name="mds_validation_mitoem2.0")
        self.dataset_dir = "mds validation/mds_validation_mitoem2.0"

    def _volume(self, name, folder="a", **extra):
        """Register by reference to an image outside the data root."""
        from volumes.services import register_volume

        image = self.external / folder / f"{name}.nii.gz"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"x")
        return register_volume(
            dataset=self.dataset, name=name, image_path=str(image),
            autodetect_shape=False, enqueue_pyramid=False, **extra,
        )

    def _touch(self, rel):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path

    def _artifacts(self, stem):
        """Every generated file production keeps under one mask stem."""
        d, t = self.dataset_dir, self.MTIME
        return [self._touch(rel) for rel in (
            f"{d}/{stem}.tif",
            f"{d}/{stem}.tif.write.lock",
            f"{d}/{stem}.track-preview-before.tif",
            f"{d}/metadata/{stem}_metadata.json",
            f"{d}/metadata/{stem}_metadata.json.bak",
            f"{d}/embeddings/sam2-hiera-l/{stem}_z_0_{t}.npz",
            f"{d}/embeddings/sam2-hiera-l/{stem}_z_0_{t}.npz.lock",
            f"{d}/embeddings/sam2-hiera-l/{stem}_z_0_{t}.npz#up1024",
            f"{d}/embeddings/vits/{stem}_z_1_{t}.npy",
            f"{d}/embeddings/vits/{stem}_z_1_{t}.npy.lock",
            f"{d}/pyramids/{stem}.zarr/1/c/0/0/0",
            f"{d}/pyramids/{stem}.region.zarr/zarr.json",
            f"{d}/pyramids/{stem}.zarr.building/zarr.json",
            f"{d}/pyramids/{stem}.zarr.previous/zarr.json",
        )]

    def _task(self, volume):
        from annotation.models import AnnotationTask

        return AnnotationTask.objects.create(
            project=self.project, volume=volume, z_start=0, z_end=8,
            y_start=0, y_end=8, x_start=0, x_end=8, task_type="manual_annotation",
        )

    def _delete(self, url):
        """Over HTTPS, as the browser reaches production behind its proxy —
        with ``SECURE_SSL_REDIRECT`` on, a plain-HTTP request only gets a 301."""
        return self.api.delete(url, secure=True)

    def _pin(self, volume, basename):
        volume.metadata = {**(volume.metadata or {}), "working_mask_basename": basename}
        volume.save(update_fields=["metadata"])

    def assertGone(self, paths):
        for path in paths:
            self.assertFalse(path.exists(), path)

    def assertKept(self, paths):
        for path in paths:
            self.assertTrue(path.exists() or path.is_symlink(), path)

    def test_volume_delete_removes_its_files_and_keeps_its_neighbour(self):
        from volumes.models import Volume

        doomed = self._volume("me2-podo_train01_0000")
        neighbour = self._volume("me2-beta_train01_0000")
        doomed_files = self._artifacts("me2-podo_train01_0000_mask")
        neighbour_files = self._artifacts("me2-beta_train01_0000_mask")
        approved_rel = f"{self.dataset_dir}/approved/me2-podo_train01_0000_mask_approved_s7.tif"
        approved = self._touch(approved_rel)
        Volume.objects.filter(pk=doomed.pk).update(label_path=approved_rel)
        upload = self._touch(f"submissions/task_{self._task(doomed).pk}/label.tif")

        self.assertEqual(self._delete(f"/api/volumes/{doomed.pk}/").status_code, 409)
        response = self._delete(f"/api/volumes/{doomed.pk}/?force=true")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertGone([*doomed_files, approved, upload])
        self.assertKept([*neighbour_files, self.external / "a" / "me2-podo_train01_0000.nii.gz"])

    def test_dataset_delete_leaves_nothing_behind(self):
        self._volume("me2-podo_train01_0000")
        self._volume("me2-beta_train01_0000")
        self._artifacts("me2-podo_train01_0000_mask")
        self._artifacts("me2-beta_train01_0000_mask")
        # Leftovers no current naming rule claims still go with the dataset.
        self._touch(f"{self.dataset_dir}/me2-retired_train01_0000_mask.tif.write.lock")
        self._touch(f"{self.dataset_dir}/embeddings/vits/me2-retired_train01_0000_mask_z_2_1.npy")

        response = self._delete(f"/api/datasets/{self.dataset.pk}/?force=true")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse((self.root / self.dataset_dir).exists())
        # The project still exists: its folder stays, empty.
        self.assertEqual(list((self.root / "mds validation").iterdir()), [])
        self.assertKept([self.external / "a" / "me2-podo_train01_0000.nii.gz"])

    def test_project_delete_removes_the_project_folder(self):
        self._volume("me2-podo_train01_0000")
        self._artifacts("me2-podo_train01_0000_mask")

        response = self._delete(f"/api/projects/{self.project.pk}/?force=true")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse((self.root / "mds validation").exists())

    def test_a_suffixed_volume_takes_its_unsuffixed_draft_with_it(self):
        volume = self._volume("608-864_8192-10240_5120-7168_im")
        self._pin(volume, f"608-864_8192-10240_5120-7168_im_v{volume.pk}_mask.tif")
        current = self._artifacts(f"608-864_8192-10240_5120-7168_im_v{volume.pk}_mask")
        draft = self._artifacts("608-864_8192-10240_5120-7168_im_mask")

        response = self._delete(f"/api/volumes/{volume.pk}/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertGone([*current, *draft])

    def test_the_unsuffixed_name_stays_while_a_sibling_owns_it(self):
        from annotation.label_paths import working_mask_stem

        owner = self._volume("case", folder="a")
        suffixed = self._volume("case", folder="b")
        self.assertEqual(working_mask_stem(owner), "case_mask")
        self.assertEqual(working_mask_stem(suffixed), f"case_v{suffixed.pk}_mask")
        owned = self._artifacts("case_mask")
        mine = self._artifacts(f"case_v{suffixed.pk}_mask")

        response = self._delete(f"/api/volumes/{suffixed.pk}/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertGone(mine)
        self.assertKept(owned)

    def test_registered_sources_in_the_root_and_symlinks_are_never_removed(self):
        registered_rel = f"{self.dataset_dir}/me2-stem_train02_0000_mask.tif"
        volume = self._volume(
            "me2-stem_train02_0000", label_path=registered_rel, label_type="partial",
        )
        registered = self._touch(registered_rel)
        outside = self.external / "elsewhere.npz"
        outside.write_bytes(b"x")
        link = self.root / f"{self.dataset_dir}/embeddings/sam2-hiera-l/me2-stem_train02_0000_mask_z_9_{self.MTIME}.npz"
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(outside, link)

        response = self._delete(f"/api/volumes/{volume.pk}/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertKept([registered, outside, link])
