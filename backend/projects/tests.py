import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

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

    def test_deleting_a_dataset_removes_its_folder(self):
        self._generate(self._volume("case_00"))

        with self.captureOnCommitCallbacks(execute=True):
            delete_dataset(self.dataset)

        self.assertFalse((self.root / "Study" / "CellMap").exists())
        self.assertTrue((self.root / "Study").is_dir())

    def test_a_dataset_folder_holding_other_files_is_kept(self):
        self._generate(self._volume("case_00"))
        stray = self._touch("Study/CellMap/notes.txt")

        with self.captureOnCommitCallbacks(execute=True):
            delete_dataset(self.dataset)

        self.assertTrue(stray.exists())
        self.assertFalse((self.root / "Study" / "CellMap" / "pyramids").exists())

    def test_deleting_a_project_removes_its_folder(self):
        self._generate(self._volume("case_00"))

        with self.captureOnCommitCallbacks(execute=True):
            delete_project(self.project)

        self.assertFalse((self.root / "Study").exists())
